"""
HEXA SEO Automation — Flask web app.

Run:  python app.py   →  open http://localhost:5000
"""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, render_template, request, send_from_directory,
    stream_with_context,
)

from seo import pipeline
from seo.scraper import fetch_logo_bytes

load_dotenv()

app = Flask(__name__)
OUTPUT_DIR = Path("outputs")


@app.route("/")
def index():
    return render_template(
        "index.html",
        brand_website=os.getenv("BRAND_WEBSITE", "https://hexaclimate.com"),
        has_claude=bool(os.getenv("ANTHROPIC_API_KEY")),
        has_gemini=bool(os.getenv("GEMINI_API_KEY")),
    )


@app.route("/api/generate", methods=["POST"])
def generate():
    """Kick off a run and stream progress as newline-delimited JSON (NDJSON)."""
    try:
        website = (request.form.get("website") or
                   os.getenv("BRAND_WEBSITE", "https://hexaclimate.com")).strip()
        extra = (request.form.get("extra") or "").strip()
        make_images = request.form.get("make_images", "true") != "false"
        max_pages = int(os.getenv("MAX_CRAWL_PAGES", "12"))

        primary_urls = pipeline.parse_urls(request.form.get("primary_sources", ""))
        secondary_urls = pipeline.parse_urls(request.form.get("secondary_sources", ""))

        fmt = (request.form.get("format") or "paragraph").lower()
        try:
            target_words = int(request.form.get("target_words") or 1400)
        except ValueError:
            target_words = 1400

        if "csv" in request.files and request.files["csv"].filename:
            keywords = pipeline.parse_keywords(request.files["csv"])
        else:
            keywords = pipeline.parse_keywords(request.form.get("keywords", ""))

        if not keywords:
            return jsonify({"error": "No keywords found. Upload a CSV or paste "
                                     "one keyword per line."}), 400

        if not os.getenv("ANTHROPIC_API_KEY"):
            return jsonify({"error": "ANTHROPIC_API_KEY is not set on the server. "
                                     "Add it in Render → Environment."}), 400
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(tb, flush=True)  # surfaces in Render logs
        return jsonify({"error": f"{type(exc).__name__}: {exc}",
                        "trace": tb.splitlines()[-6:]}), 500

    def event_stream():
        try:
            for event in pipeline.run(
                keywords, website,
                primary_urls=primary_urls,
                secondary_urls=secondary_urls,
                extra_instructions=extra,
                make_images=make_images,
                max_pages=max_pages,
                fmt=fmt,
                target_words=target_words,
            ):
                yield json.dumps(event) + "\n"
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            print(tb, flush=True)
            yield json.dumps({
                "event": "error", "fatal": True,
                "message": f"{type(exc).__name__}: {exc or '(no message)'}",
                "trace": tb.splitlines()[-6:],
            }) + "\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/api/logo")
def logo():
    """Proxy the live Hexa logo, with the bundled SVG as fallback."""
    logo_url = request.args.get("url")
    if logo_url:
        result = fetch_logo_bytes(logo_url)
        if result:
            content, content_type = result
            return Response(content, mimetype=content_type)
    return send_from_directory("static", "logo.svg")


@app.route("/outputs/<path:filename>")
def outputs(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"\n  HEXA SEO Automation running →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
