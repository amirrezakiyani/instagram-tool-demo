# Downloadino experimental backend

این سرویس برای تست رایگان ساخته شده و فقط لینک‌های عمومی اینستاگرام را پردازش می‌کند. فایل‌ها در دیسک دائمی ذخیره نمی‌شوند. به‌دلیل تغییرات مداوم اینستاگرام، سرویس ممکن است بدون اطلاع قبلی محدود یا ناپایدار شود.

## اجرا

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## API

`POST /api/download`

```json
{"url":"https://www.instagram.com/reel/.../","kind":"video"}
```

مقادیر `kind`: `video`, `photo`, `audio`.

این سرویس نباید برای محتوای خصوصی یا محتوایی که کاربر اجازهٔ استفاده از آن را ندارد به کار رود.
