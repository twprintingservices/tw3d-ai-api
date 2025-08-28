from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import tempfile, os

from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
from OCP.BRepGProp import brepgprop_VolumeProperties
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import brepbndlib_Add

app = FastAPI(title="TW 3D AI Quote API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DENSITY = {"PLA Basic":1.24,"PLA Tough":1.24,"PETG Basic":1.27,"ABS":1.05,"ASA":1.07,"PC":1.20,"PA (Nylon)":1.14,"TPU 95A":1.21}
BAMBU_PRICES = {"PLA Basic":20,"PLA Tough":26,"PETG Basic":25,"ABS":26,"ASA":26,"PC":30,"PA (Nylon)":40,"TPU 95A":30}

def read_shape(path):
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    if status != IFSelect_RetDone:
        raise ValueError("STEP read failed")
    reader.TransferRoots()
    return reader.OneShape()

def bbox_mm(shape):
    box = Bnd_Box(); brepbndlib_Add(shape, box)
    xmin,ymin,zmin,xmax,ymax,zmax = box.Get()
    return [(xmax-xmin)*1000,(ymax-ymin)*1000,(zmax-zmin)*1000]

def volume_cm3(shape):
    exp = TopExp_Explorer(shape, TopAbs_SOLID); total=0
    while exp.More():
        solid=exp.Current(); props=GProp_GProps(); brepgprop_VolumeProperties(solid,props); total+=props.Mass(); exp.Next()
    if total==0: props=GProp_GProps(); brepgprop_VolumeProperties(shape,props); total=props.Mass()
    return total*1_000_000

@app.post("/quote")
async def quote(file:UploadFile=File(...), material:str=Form("PLA Basic")):
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(delete=False,suffix=".step") as t:
            t.write(await file.read()); tmp=t.name
        shape=read_shape(tmp)
        bbox=bbox_mm(shape); vol=volume_cm3(shape)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error":str(e)})
    finally:
        if tmp and os.path.exists(tmp): os.remove(tmp)
    density=DENSITY.get(material,1.2); grams=vol*density*0.35
    pricePerKg=BAMBU_PRICES.get(material,25)
    materialCost=pricePerKg*(grams/1000)*1.1
    largest=max(bbox); timeH=vol*0.03+(largest/80)
    machine=12*timeH; electric=0.2*0.15*timeH; labor=30*0.33
    base=materialCost+machine+electric+labor; subtotal=base*1.1; final=subtotal*1.35
    return {"volume_cm3":vol,"bbox_mm":bbox,"estimated_price":round(final,2),"material":material}
