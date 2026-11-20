# 濫用藥物實驗室工具箱

本專案依據 [SPEC.md](./SPEC.md) 建立，為實驗室內部使用的模組化網頁工具箱。

## 啟動方式

1. 建立並啟用虛擬環境（首次執行）：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. 啟動網站：
   ```powershell
   python run.py
   ```
3. 瀏覽器開啟：`http://localhost:8000`（同區網同事可開啟 `http://<主機IP>:8000`）
4. 首次登入預設管理員帳號：`admin` / 密碼：`admin1234`（登入後請至「帳號管理」修改密碼並建立同事帳號）。

## 之後每週新增小工具時要改哪裡？

1. **建立工具程式**：在 `app/tools/` 新增一個 `.py` 檔（可直接複製 `app/tools/_template.py` 修改）。
2. **建立工具頁面**：在 `app/templates/` 新增該工具的 HTML 模板（繼承 `base.html`）。
3. **註冊工具**：打開 `app/tools/registry.py`，將新工具匯入並加入 `TOOL_REGISTRY` 清單中（指定六大分類之一：`收樣`、`初篩檢驗`、`確認檢驗`、`品管工具`、`帳款`、`藥品管理`）。
