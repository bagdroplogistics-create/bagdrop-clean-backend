```python id="rb6x8w"
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {
        "message": "Bagdrop API",
        "status": "ok"
    }
```
