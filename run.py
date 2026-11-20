import uvicorn

if __name__ == "__main__":
    # 監聽 0.0.0.0:8000，讓同一區域網路內的實驗室同事也能透過主機 IP 連線
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
