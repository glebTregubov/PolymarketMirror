# Polymarket Strategy Mirror

A delta-neutral strategy analysis tool for Polymarket ladder events (binary options on crypto price levels). Fetches live spot prices from Binance and calculates optimal unit allocations across strike prices for balanced options portfolios.

![Python](https://img.shields.io/badge/python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104-green)
![License](https://img.shields.io/badge/license-MIT-blue)

## Features

- ✅ **Delta-neutral strategy calculations** with automatic position sizing
- ✅ **Real-time price feeds** from Binance (BTC, ETH, SOL)
- ✅ **Live Polymarket data** extraction via HTML parsing
- ✅ **Event selection UI** with curated crypto ladder events
- ✅ **APY calculations** and profit projections
- ✅ **Budget enforcement** with risk cap constraints
- ✅ **Interactive web interface** built with HTMX + Tailwind CSS

## Tech Stack

### Backend
- **FastAPI** - Modern async Python web framework
- **httpx** - Async HTTP client for external APIs
- **BeautifulSoup4** - HTML parsing for Polymarket events
- **uvicorn** - ASGI server

### Frontend
- **Jinja2** - Server-side template rendering
- **Tailwind CSS** - Utility-first styling (CDN)
- **HTMX** - Dynamic interactions without JavaScript
- **Alpine.js** - Minimal client-side reactivity

## Quick Start

### Option 1: Run with Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/glebTregubov/PolymarketMirror.git
cd PolymarketMirror

# Start with docker-compose
docker-compose up -d

# Access at http://localhost:5000
```

### Option 2: Run Locally with Python

```bash
# Clone the repository
git clone https://github.com/glebTregubov/PolymarketMirror.git
cd PolymarketMirror

# Install uv (fast Python package manager)
pip install uv

# Install dependencies
uv pip install -r pyproject.toml

# Run the application
uvicorn app:app --host 0.0.0.0 --port 5000 --reload

# Access at http://localhost:5000
```

## Project Structure

```
PolymarketMirror/
├── app.py                  # Main FastAPI application
├── strategy_engine.py      # Delta-neutral calculation engine
├── binance_client.py       # Binance API client (spot prices)
├── polymarket_parser.py    # Polymarket HTML parser
├── templates/              # Jinja2 HTML templates
│   ├── index.html         # Landing page
│   ├── events.html        # Event selection menu
│   └── mirror.html        # Main analysis view
├── static/                # Static assets (CSS, images)
├── Dockerfile             # Docker image configuration
├── docker-compose.yml     # Docker orchestration
└── pyproject.toml         # Python dependencies
```

## API Endpoints

- `GET /` - Landing page with event input
- `GET /events` - Browse available crypto events
- `GET /mirror` - Strategy analysis for specific event
- `GET /demo` - Demo mode with test data
- `GET /health` - Health check endpoint

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SESSION_SECRET` | No | Auto-generated | Secret key for sessions |

## How It Works

1. **Fetch Event Data**: Parses Polymarket event page to extract strikes and YES/NO prices
2. **Get Spot Price**: Fetches current crypto price from Binance as anchor point
3. **Calculate Strategy**: Computes optimal position sizing for delta-neutral portfolio
4. **Generate Recommendations**: Outputs buy orders with P&L projections and APY

## Development

### Running Tests
```bash
# Run with pytest (when tests are added)
pytest tests/
```

### Code Structure
- **Pure calculation logic** in `strategy_engine.py` (testable, no side effects)
- **API clients** in separate modules (`binance_client.py`, `polymarket_parser.py`)
- **Route handlers** in `app.py` (thin layer, delegates to services)

## Deployment

### Replit (Production)
This project is optimized for Replit deployment with:
- Lazy-loaded clients for fast startup
- `/health` endpoint for health checks
- No database requirements (stateless)

### Docker (Self-hosted)
```bash
docker build -t polymarket-mirror .
docker run -p 5000:5000 polymarket-mirror
```

## Roadmap

- [ ] Dynamic event discovery (parse /crypto page)
- [ ] WebSocket support for real-time updates
- [ ] Core-NO and Core-YES strategy modes
- [ ] Historical backtesting
- [ ] User portfolio tracking
- [ ] Settings UI for parameters

## Contributing

Contributions welcome! Please open an issue or submit a pull request.

## License

MIT License - see LICENSE file for details

## Acknowledgments

- Data provided by [Polymarket](https://polymarket.com)
- Price feeds from [Binance](https://www.binance.com)

---

**Note**: This tool is for educational purposes. Always do your own research before trading.
