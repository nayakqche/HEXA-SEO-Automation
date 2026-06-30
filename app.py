"""
HEXA SEO Automation — Flask web app.

Run:  python app.py   →  open http://localhost:5000

Flow: upload a CSV of keywords + confirm the Hexa Climate site, and the app
scrapes the site for grounding, writes an SEO blog per keyword with Claude,
and generates a hero image per post with Gemini. Progress streams live.
"""

from __future__ import annotations

import json
import os
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
    website = (request.form.get("website") or
               os.getenv("BRAND_WEBSITE", "https://hexaclimate.com")).strip()
    extra = (request.form.get("extra") or "").strip()
    make_images = request.form.get("make_images", "true") != "false"
    max_pages = int(os.getenv("MAX_CRAWL_PAGES", "12"))

    # Keywords from an uploaded CSV file, or pasted text in the form.
    if "csv" in request.files and request.files["csv"].filename:
        keywords = pipeline.parse_keywords(request.files["csv"])
    else:
        keywords = pipeline.parse_keywords(request.form.get("keywords", ""))

    if not keywords:
        return jsonify({"error": "No keywords found. Upload a CSV or paste "
                                 "one keyword per line."}), 400

    def event_stream():
        try:
            for event in pipeline.run(
                keywords, website,
                extra_instructions=extra,
                make_images=make_images,
                max_pages=max_pages,
            ):
                yield json.dumps(event) + "\n"
        except Exception as exc:  # noqa: BLE001
            yield json.dumps({"event": "error", "fatal": True,
                              "message": str(exc)}) + "\n"

    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/api/logo")
def logo():
    """Proxy the live Hexa logo (taken from the brand site) with SVG fallback."""
    logo_url = request.args.get("url")
    if logo_url:
        result = fetch_logo_bytes(logo_url)
        if result:
            content, content_type = result
            return Response(content, mimetype=content_type)
    # Fallback: a clean Hexa-style hexagon mark so the UI always renders.
    return send_from_directory("static", "logo.svg")


@app.route("/outputs/<path:filename>")
def outputs(filename):
    """Serve generated blog files, images, and HTML previews."""
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"\n  HEXA SEO Automation running →  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
