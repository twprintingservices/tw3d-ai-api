from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile, os

# pythonocc-core (OpenCascade) imports
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_SOLID
from OCC.Core.BRepGProp import brepgprop_VolumeProperties
from OCC.Core.GProp import GProp_GProps
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib_Add

app = FastAPI(title="TW 3D AI Quote API (units-aware)")

NETLIFY_ORIGIN = os.getenv("NETLIFY_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[NETLIFY_ORIGIN] if NETLIFY_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Materials
DENSITY = {"PLA Basic":1.24,"PLA Tough":1.24,"PETG Basic":1.27,"ABS":1.05,"ASA":1.07,"PC":1.20,"PA (Nylon)":1.14,"TPU 95A":1.21}
BAMBU_PRICES = {"PLA Basic":20,"PLA Tough":26,"PETG Basic":25,"ABS":26,"ASA":26,"PC":30,"PA (Nylon)":40,"TPU 95A":30}

def sniff_units_from_step(path:str):
    """Return (scale_mm_per_unit, label) by scanning STEP text header.
    Default to mm if unknown. Heuristics:
      - INCH -> 25.4 mm
      - FOOT/FEET -> 304.8 mm
      - SI_UNIT(.MILLI.,.METRE.) -> mm (1.0)
      - SI_UNIT(.CENTI.,.METRE.) -> cm (10.0 mm)
      - SI_UNIT(.DECI.,.METRE.) -> dm (100.0 mm)
      - SI_UNIT($,.METRE.) or SI_UNIT(.NONE.,.METRE.) -> metre (1000.0 mm)
    """
    try:
        with open(path, 'rb') as f:
            blob = f.read(200_000)  # first 200 KB is enough for header
        try:
            text = blob.decode('utf-8', errors='ignore').upper()
        except Exception:
            text = blob.decode('latin-1', errors='ignore').upper()
    except Exception:
        return 1.0, "mm (default)"
    # Inches / feet first
    if "INCH" in text or "INCHES" in text or "IN" in text and "INCH_UNIT" in text:
        return 25.4, "inch"
    if "FOOT" in text or "FEET" in text:
        return 304.8, "foot"
    # SI units
    if "SI_UNIT" in text and "MILLI" in text and "METRE" in text:
        return 1.0, "mm"
    if "SI_UNIT" in text and "CENTI" in text and "METRE" in text:
        return 10.0, "cm"
    if "SI_UNIT" in text and "DECI" in text and "METRE" in text:
        return 100.0, "dm"
    if "SI_UNIT" in text and "METRE" in text:
        return 1000.0, "m"
    # Fallback: assume mm
    return 1.0, "mm (assumed)"

def read_shape_from_step(path: str):
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise ValueError("Failed to read STEP file")
    reader.TransferRoots()
    shape = reader.OneShape()
    return shape

def bbox_mm(shape, scale_mm_per_unit: float):
    box = Bnd_Box()
    brepbndlib_Add(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    dx = (xmax - xmin) * scale_mm_per_unit
    dy = (ymax - ymin) * scale_mm_per_unit
    dz = (zmax - zmin) * scale_mm_per_unit
    return [max(dx,0.0), max(dy,0.0), max(dz,0.0)]

def volume_cm3(shape, scale_mm_per_unit: float):
    # Volume is in "unit^3". Convert to cm^3 using scale:
    # unit^3 -> (scale_mm_per_unit^3) mm^3 -> /1000 => cm^3
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    total_units3 = 0.0
    while exp.More():
        solid = exp.Current()
        props = GProp_GProps()
        brepgprop_VolumeProperties(solid, props)
        total_units3 += props.Mass()  # volume in unit^3
        exp.Next()
    if total_units3 == 0.0:
        props = GProp_GProps()
        brepgprop_VolumeProperties(shape, props)
        total_units3 = props.Mass()
    mm3 = total_units3 * (scale_mm_per_unit ** 3)
    cm3 = mm3 / 1000.0
    return cm3

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/quote")
async def quote(file: UploadFile = File(...), material: str = Form("PLA Basic")):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".step") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        scale_mm, unit_label = sniff_units_from_step(tmp_path)
        shape = read_shape_from_step(tmp_path)
        bbox = bbox_mm(shape, scale_mm)
        vol_cm3 = volume_cm3(shape, scale_mm)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except Exception: pass

    # Pricing (hidden from user; Bambu Lab prices)
    density = DENSITY.get(material, 1.2)
    grams = vol_cm3 * density * 0.35  # 35% solid equivalent
    price_per_kg = BAMBU_PRICES.get(material, 25)

    filament_per_kg = float(os.getenv("FILAMENT_PER_KG", str(price_per_kg)))
    waste_pct = float(os.getenv("WASTE_PCT", "10"))
    m_rate = float(os.getenv("MACHINE_RATE", "12"))
    kW = float(os.getenv("AVG_KW", "0.2"))
    kWh_cost = float(os.getenv("KWH_COST", "0.15"))
    lab_h = float(os.getenv("LABOR_HOURS", "0.33"))
    lab_rate = float(os.getenv("LABOR_RATE", "30"))
    oh_pct = float(os.getenv("OVERHEAD_PCT", "10"))
    mu_pct = float(os.getenv("MARKUP_PCT", "35"))

    material_cost = filament_per_kg * (grams/1000.0) * (1 + waste_pct/100.0)
    largest = max(bbox) if bbox else 0.0
    time_h = vol_cm3*0.03 + (largest/80.0)
    machine_cost = m_rate * time_h
    electric_cost = kW * kWh_cost * time_h
    labor_cost = lab_rate * lab_h
    base = material_cost + machine_cost + electric_cost + labor_cost
    subtotal = base * (1 + oh_pct/100.0)
    final_price = subtotal * (1 + mu_pct/100.0)

    return {
        "volume_cm3": round(vol_cm3, 3),
        "bbox_mm": [round(b, 3) for b in bbox],
        "estimated_price": round(final_price, 2),
        "material": material,
        "units_detected": unit_label
    }
