from fastapi import FastAPI

app = FastAPI(title="IT Operations Agent API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "service": "it-operations-agent-api",
        "status": "ok",
    }
