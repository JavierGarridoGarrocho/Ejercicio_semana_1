from flask import Flask, jsonify, render_template, request

from marca_scraper import scrape_marca_news

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/scrape")
def api_scrape():
    limit_raw = request.args.get("limit", default="5")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid `limit` value. Must be an integer."}), 400

    # Clamp to avoid overwhelming the site / your machine.
    limit = max(1, min(limit, 20))

    try:
        items = scrape_marca_news(limit=limit)
    except Exception as exc:  # pragma: no cover
        return jsonify({"error": str(exc)}), 500

    return jsonify({"items": items})


if __name__ == "__main__":
    # Local dev server. For production, use a proper WSGI server.
    app.run(host="127.0.0.1", port=5000, debug=True)

