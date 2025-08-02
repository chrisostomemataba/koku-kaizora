from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router

app = FastAPI(
    title="Koku Kaizora - Therapy Center Timetable System",
    description="Smart timetable generation and management for child therapy centers",
    version="1.0.0",
    docs_url="/",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

@app.get("/health")
def health_check():
    return {"status": "healthy", "message": "Therapy center timetable system is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)