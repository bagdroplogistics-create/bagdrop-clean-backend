from fastapi import FastAPI, APIRouter

app = FastAPI()

api_router = APIRouter(prefix="/api")

@api_router.get("/")
async def root():
    return {"message": "Bagdrop API"}

app.include_router(api_router)
