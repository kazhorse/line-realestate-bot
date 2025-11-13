from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.get("/")
async def root():
    # 動作確認用
    return {"message": "LINE Realestate Bot (health check OK)"}


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    # Railway のログに表示されるように
    print("Webhook received:", body)
    return JSONResponse(content={"status": "ok"})
