export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/api/download") {
      return env.ASSETS.fetch(request);
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "POST" } });
    }

    const json = (data, status = 200) => new Response(JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }
    });

    try {
      const body = await request.json();
      const source = new URL(String(body?.url || ""));
      if (source.protocol !== "https:" ||
          !["instagram.com", "www.instagram.com"].includes(source.hostname.toLowerCase()) ||
          !/^\/(reel|reels|p|tv)\/[A-Za-z0-9_-]+\/?$/.test(source.pathname)) {
        return json({ error: "لینک معتبر ریلز یا پست عمومی اینستاگرام را وارد کن." }, 400);
      }

      const pageResponse = await fetch(source.href, {
        redirect: "follow",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.8"
        },
        signal: AbortSignal.timeout(18000)
      });

      if (!pageResponse.ok) {
        return json({ error: "اینستاگرام فعلاً اجازهٔ دریافت این پست را نمی‌دهد. ممکن است خصوصی باشد؛ کمی بعد دوباره تلاش کن." }, 422);
      }

      const html = await pageResponse.text();
      let mediaUrl = "";

      // Prefer Instagram's embedded video URL, then Open Graph video metadata.
      const embedded = html.match(/"video_url"\s*:\s*"((?:\\\\.|[^"\\\\])+)"/);
      if (embedded) {
        try { mediaUrl = JSON.parse('"' + embedded[1] + '"'); } catch (_) {
          mediaUrl = embedded[1].replace(/\\u0026/g, "&").replace(/\\\//g, "/");
        }
      }

      if (!mediaUrl) {
        const tags = html.match(/<meta\b[^>]*>/gi) || [];
        for (const tag of tags) {
          if (!/property\s*=\s*["']og:video(?::secure_url)?["']/i.test(tag)) continue;
          const value = tag.match(/content\s*=\s*["']([^"']+)["']/i);
          if (value) { mediaUrl = value[1].replace(/&amp;/g, "&"); break; }
        }
      }

      if (!mediaUrl) {
        return json({ error: "ویدیوی قابل دانلود پیدا نشد. اینستاگرام گاهی دسترسی خودکار به ریلزها را مسدود می‌کند؛ لینک عمومی دیگری را امتحان کن." }, 422);
      }

      let media;
      try { media = new URL(mediaUrl); } catch (_) { return json({ error: "لینک فایل ویدیو معتبر نبود." }, 422); }
      const mediaHost = media.hostname.toLowerCase();
      if (media.protocol !== "https:" ||
          !(mediaHost.endsWith(".cdninstagram.com") || mediaHost === "cdninstagram.com" ||
            mediaHost.endsWith(".fbcdn.net") || mediaHost === "fbcdn.net")) {
        return json({ error: "منبع فایل ویدیو تأیید نشد." }, 422);
      }

      const fileResponse = await fetch(media.href, {
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
          "Referer": "https://www.instagram.com/"
        },
        redirect: "follow",
        signal: AbortSignal.timeout(25000)
      });

      if (!fileResponse.ok || !fileResponse.body) {
        return json({ error: "دریافت فایل از اینستاگرام ناموفق بود. دوباره تلاش کن." }, 502);
      }

      const headers = new Headers({
        "Content-Type": "video/mp4",
        "Content-Disposition": 'attachment; filename="downloadino-instagram.mp4"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"
      });
      const length = fileResponse.headers.get("Content-Length");
      if (length) headers.set("Content-Length", length);
      return new Response(fileResponse.body, { status: 200, headers });
    } catch (error) {
      if (error instanceof SyntaxError) return json({ error: "درخواست معتبر نیست." }, 400);
      return json({ error: "ارتباط با اینستاگرام برقرار نشد. لطفاً دوباره تلاش کن." }, 502);
    }
  }
};
