# Polymarket Strategy Mirror

## Overview

This application is a delta-neutral strategy analysis tool for Polymarket ladder events (binary options on crypto price levels). It fetches live spot prices from Binance and calculates optimal unit allocations across strike prices based on user-defined budget, risk tolerance, and directional bias. The system provides actionable order recommendations for traders seeking to construct balanced options portfolios.

**Current Status: MVP v1.0 - PRODUCTION READY**
- ✅ Symmetric delta-neutral strategy calculations
- ✅ Budget enforcement with remaining_budget tracking
- ✅ Risk cap implementation (max loss constraint)
- ✅ Real-time Binance spot prices via data-api.binance.vision (BTC/ETH/SOL)
- ✅ Demo mode with test data (`/demo` endpoint)
- ✅ Live Polymarket data extraction from __NEXT_DATA__ JSON with real YES/NO prices
- ✅ Works with real events (e.g., `what-price-will-ethereum-hit-september-29-october-5`)

**Next Phase:**
- WebSocket support for real-time price updates
- Core-NO and Core-YES strategy modes
- Historical data analysis and backtesting

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
**Problem**: Need a lightweight, interactive UI without complex build tooling on Replit  
**Solution**: Server-side rendering with Jinja2 templates + HTMX for dynamic updates + Tailwind CSS for styling  
**Rationale**: 
- No Node.js build step required - works instantly on Replit
- HTMX enables interactivity via HTML attributes without writing JavaScript
- Alpine.js provides minimal client-side reactivity where needed
- Tailwind CDN eliminates CSS compilation

**Pros**: Fast development, zero build config, Replit-friendly  
**Cons**: Limited to SSR patterns, less suitable for complex SPAs

### Backend Architecture
**Problem**: Need async HTTP clients for external APIs and fast routing  
**Solution**: FastAPI with async/await pattern  
**Components**:
- `app.py`: Main FastAPI application with route handlers
- `strategy_engine.py`: Pure calculation logic for position sizing, P&L, and order generation
- `binance_client.py`: Async wrapper for Binance Spot API (price feeds)
- `polymarket_parser.py`: HTML scraping and data extraction for Polymarket events

**Design Pattern**: Service layer separation - API clients, business logic (strategy engine), and web handlers are cleanly separated

**Pros**: High performance async I/O, OpenAPI docs, type hints  
**Cons**: Requires async programming discipline throughout

### Data Flow Pattern
**Problem**: Need to combine data from multiple external sources (Polymarket + Binance) for each request  
**Solution**: Request-scoped async composition  
**Flow**:
1. User submits event slug + parameters (budget, bias, risk cap)
2. Backend fetches event data from Polymarket (markets, strikes, questions)
3. Backend fetches current spot price from Binance for anchor
4. Strategy engine calculates optimal allocations using fetched data
5. Template renders combined view with event mirror + recommendations

**Alternatives Considered**: WebSocket subscriptions for live updates, background workers  
**Chosen Approach**: Simple request/response with optional polling  
**Rationale**: MVP simplicity, avoids state management complexity

### Calculation Engine
**Problem**: Need to compute position sizing, risk metrics, and expected value across multiple strikes  
**Solution**: Standalone `StrategyEngine` class with configurable parameters  
**Inputs**:
- Fee structure (settlement fees, slippage allowance)
- Risk parameters (beta for smoothing, risk cap)
- Market data (strikes, YES/NO prices)
- User preferences (budget, bias)

**Outputs**: `OrderRecommendation` and `PortfolioSummary` dataclasses

**Pros**: Testable in isolation, reusable, pure functions  
**Cons**: Currently stateless - doesn't persist user portfolios

### Error Handling Strategy
**Problem**: External APIs may fail or return unexpected data  
**Solution**: Graceful degradation with fallbacks  
**Pattern**:
- Binance client returns `Optional[float]` - None on error
- Demo mode provides hardcoded sample data when APIs unavailable
- Error template displays user-friendly messages

## External Dependencies

### Third-Party APIs

**Binance Data API**
- **Purpose**: Real-time spot price feeds for BTC, ETH, SOL
- **Endpoint**: `https://data-api.binance.vision/api/v3/ticker/bookTicker`
- **Method**: Uses bid/ask prices to calculate mid price: `(bid + ask) / 2.0`
- **Authentication**: None required (public endpoint)
- **Rate Limits**: Managed via httpx async client with 10s timeout
- **Regional Access**: Uses data-api.binance.vision to bypass regional restrictions
- **Fallback**: Demo mode uses hardcoded anchor price if API unavailable

**Polymarket Next.js Data Extraction**
- **Purpose**: Event metadata, market questions, strike prices, YES/NO quotes
- **Current Status**: ✅ Production-ready - extracts from `__NEXT_DATA__` JSON embedded in HTML
- **Data Source**: Parses Next.js server-side rendered data from `<script id="__NEXT_DATA__">`
- **Price Source**: `outcomePrices` array contains live YES/NO probabilities
- **Strike Extraction**: Regex pattern matching from market questions
- **Markets**: Successfully extracts 10-20 markets per event with real prices
- **Future Enhancement**: Direct API integration for WebSocket real-time updates

### Python Libraries

**FastAPI** - Modern async web framework  
**httpx** - Async HTTP client for external API calls  
**Jinja2** - Template rendering engine  
**BeautifulSoup4** - HTML parsing for Polymarket scraping  
**dataclasses** - Type-safe data structures for domain models  

### Frontend Libraries (CDN)

**Tailwind CSS** - Utility-first styling via CDN  
**HTMX** - HTML-over-the-wire interactions  
**Alpine.js** - Minimal JavaScript reactivity  

### Infrastructure

**Replit Platform** - Hosting and runtime environment  
**Static Files**: Served via FastAPI's `StaticFiles` mount at `/static`  
**Templates**: Jinja2 templates in `/templates` directory  
**No Database**: Current architecture is stateless (in-memory only)  
**Future Consideration**: Redis for caching market data with TTL, or lightweight SQLite for user preferences