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

    // If the yt-dlp service URL is configured as a Pages environment variable,
    // proxy the real file stream through this same-origin endpoint.
    if (env.DOWNLOADER_API_URL) {
      try {
        const upstreamBase = String(env.DOWNLOADER_API_URL).replace(/\/+$/, "");
        const upstream = await fetch(upstreamBase + "/api/download", {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "video/mp4, application/json" },
          body: await request.clone().text(),
          signal: AbortSignal.timeout(120000)
        });
        const headers = new Headers();
        for (const name of ["Content-Type", "Content-Disposition", "Cache-Control", "Content-Length", "X-Content-Type-Options"]) {
          const value = upstream.headers.get(name);
          if (value) headers.set(name, value);
        }
        if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json; charset=utf-8");
        headers.set("Cache-Control", "no-store");
        return new Response(upstream.body, { status: upstream.status, headers });
      } catch (_) {
        return json({ error: "The download service is temporarily unavailable. Please try again shortly." }, 502);
      }
    }

    try {
      const body = await request.json();
      const format = body?.format || "video";
      if (!["video", "audio", "image"].includes(format)) return json({ error: "Choose Video (MP4), Audio (MP3), or Cover (JPG)." }, 400);
      if (format === "audio") return json({ error: "MP3 extraction needs the separate media-processing service, which is not configured yet. Video downloads may still work." }, 503);
      const source = new URL(String(body?.url || ""));
      if (source.protocol !== "https:" ||
          !["instagram.com", "www.instagram.com"].includes(source.hostname.toLowerCase()) ||
          !/^\/(reel|reels|p|tv)\/[A-Za-z0-9_-]+\/?$/.test(source.pathname)) {
        return json({ error: "لینک معتبر ریلز یا پست عمومی اینستاگرام را وارد کن." }, 400);
      }

      // First try Instagram's current web GraphQL media endpoint. HTML pages often
      // redirect logged-out visitors to login and omit the actual video URL.
      // This is best-effort only: Instagram can rotate the document ID or block requests.
      let graphqlMediaUrl = "";
      try {
        const shortcode = source.pathname.split("/").filter(Boolean).pop();
        const home = await fetch("https://www.instagram.com/", {
          headers: {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.8"
          },
          signal: AbortSignal.timeout(10000)
        });
        const cookieHeaders = home.headers.get("set-cookie") || "";
        const csrfMatch = cookieHeaders.match(/(?:^|[,; ]+)csrftoken=([^;,s]+)/);
        const homeHtml = await home.text();
        const csrfFromHtml = homeHtml.match(/"csrf_token"\s*:\s*"([^"]+)"/);
        const csrf = csrfMatch?.[1] || csrfFromHtml?.[1] || "";
        if (csrf) {
          const gql = await fetch("https://www.instagram.com/graphql/query", {
            method: "POST",
            headers: {
              "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
              "Accept": "*/*",
              "Content-Type": "application/x-www-form-urlencoded",
              "X-IG-App-ID": "936619743392459",
              "X-CSRFToken": csrf,
              "X-Requested-With": "XMLHttpRequest",
              "Origin": "https://www.instagram.com",
              "Referer": source.href,
              "Cookie": "csrftoken=" + csrf
            },
            body: new URLSearchParams({
              doc_id: "27128499623469141",
              variables: JSON.stringify({
                shortcode,
                "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": false
              })
            }).toString(),
            signal: AbortSignal.timeout(14000)
          });
          if (gql.ok) {
            const payload = await gql.json();
            const seen = new Set();
            const visit = (node) => {
              if (!node || typeof node !== "object") return;
              if (seen.has(node)) return;
              seen.add(node);
              if (Array.isArray(node.video_versions)) {
                const match = node.video_versions.find(v => typeof v?.url === "string" && /^https:\/\//.test(v.url));
                if (match) { graphqlMediaUrl = match.url; return; }
              }
              if (node.video_url && typeof node.video_url === "string" && /^https:\/\//.test(node.video_url)) {
                graphqlMediaUrl = node.video_url; return;
              }
              for (const value of Object.values(node)) {
                if (graphqlMediaUrl) return;
                if (value && typeof value === "object") visit(value);
              }
            };
            visit(payload);
          }
        }
      } catch (_) {
        // Continue with the HTML fallback below.
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

      if (format === "image") {
        const metaTags = html.match(/<meta\b[^>]*>/gi) || [];
        let imageUrl = "";
        for (const tag of metaTags) {
          if (!/property\s*=\s*["']og:image["']/i.test(tag)) continue;
          const value = tag.match(/content\s*=\s*["']([^"']+)["']/i);
          if (value) { imageUrl = value[1].replace(/&amp;/g, "&"); break; }
        }
        if (!imageUrl) return json({ error: "Instagram did not expose a public cover image for this post." }, 422);
        let imageSource;
        try { imageSource = new URL(imageUrl); } catch (_) { return json({ error: "The cover image URL was invalid." }, 422); }
        const imageHost = imageSource.hostname.toLowerCase();
        if (imageSource.protocol !== "https:" ||
            !(imageHost.endsWith(".cdninstagram.com") || imageHost === "cdninstagram.com" ||
              imageHost.endsWith(".fbcdn.net") || imageHost === "fbcdn.net")) {
          return json({ error: "The cover image source could not be verified." }, 422);
        }
        const imageResponse = await fetch(imageSource.href, {
          headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36", "Referer": "https://www.instagram.com/" },
          redirect: "follow",
          signal: AbortSignal.timeout(20000)
        });
        if (!imageResponse.ok || !imageResponse.body) return json({ error: "Could not retrieve the cover image. Please try again." }, 502);
        const contentType = imageResponse.headers.get("Content-Type") || "image/jpeg";
        const ext = contentType.includes("png") ? "png" : contentType.includes("webp") ? "webp" : "jpg";
        return new Response(imageResponse.body, { status: 200, headers: {
          "Content-Type": contentType,
          "Content-Disposition": 'attachment; filename="downloadino-cover.' + ext + '"',
          "Cache-Control": "no-store",
          "X-Content-Type-Options": "nosniff"
        }});
      }

      let mediaUrl = graphqlMediaUrl;

      // Prefer Instagram's embedded video URL, then Open Graph video metadata.
      const embedded = html.match(/"video_url"\s*:\s*"((?:\\.|[^"\\])*)"/);
      if (embedded) {
        try { mediaUrl = JSON.parse('"' + embedded[1] + '"'); } catch (_) {
          mediaUrl = embedded[1].replace(/\\u0026/g, "&").replace(/\\\//g, "/");
        }
      }

      // Instagram often exposes progressive MP4 URLs inside its embedded Relay payload,
      // even when video_url and og:video are absent.
      if (!mediaUrl) {
        const shortcode = source.pathname.split("/").filter(Boolean).pop();
        const needles = ['"code":"' + shortcode + '"', '"code": "' + shortcode + '"'];
        let codeIndex = -1;
        for (const needle of needles) {
          codeIndex = html.indexOf(needle);
          if (codeIndex !== -1) break;
        }
        if (codeIndex !== -1) {
          const windowText = html.slice(codeIndex, codeIndex + 50000);
          const versionsMatch = /"video_versions"\s*:\s*\[/.exec(windowText);
          if (versionsMatch) {
            const arrayStart = codeIndex + versionsMatch.index + versionsMatch[0].lastIndexOf("[");
            let depth = 0, inString = false, escaped = false, arrayEnd = -1;
            for (let i = arrayStart; i < html.length; i++) {
              const ch = html[i];
              if (inString) {
                if (escaped) escaped = false;
                else if (ch === "\\") escaped = true;
                else if (ch === '"') inString = false;
              } else if (ch === '"') inString = true;
              else if (ch === "[") depth++;
              else if (ch === "]") {
                depth--;
                if (depth === 0) { arrayEnd = i + 1; break; }
              }
            }
            if (arrayEnd > arrayStart) {
              try {
                const versions = JSON.parse(html.slice(arrayStart, arrayEnd));
                const candidate = versions.find(v => typeof v?.url === "string" && v.url.startsWith("https://"));
                if (candidate) mediaUrl = candidate.url;
              } catch (_) {}
            }
          }
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
