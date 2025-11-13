from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "LINE Realestate Bot (health check OK)"}

@app.post("/webhook")
async def webhook(request: Request):
    # LINE から送られてきた内容を一応読む（ログ用）
    body = await request.body()
    print("Webhook body:", body.decode("utf-8", errors="ignore"))

    # すぐに 200 を返すだけ（LINE 的にはこれで OK）
    return {"status": "ok"}
