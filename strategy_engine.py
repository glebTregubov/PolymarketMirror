import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from polymarket_parser import Market, StrikeMeta


@dataclass
class OrderRecommendation:
    market_id: str
    question: str
    strike: Optional[float]
    side: str
    units: int
    limit_price: float
    cost: float
    max_profit: float
    max_loss: float
    ev: Optional[float] = None


@dataclass
class PortfolioSummary:
    total_cost: float
    max_loss: float
    max_profit: float
    up_side_cost: float
    down_side_cost: float
    num_orders: int


class StrategyEngine:
    def __init__(
        self,
        fee_settlement: float = 0.02,
        slippage_limit: float = 0.005,
        beta: float = 10.0
    ):
        self.fee_settlement = fee_settlement
        self.slippage_limit = slippage_limit
        self.beta = beta
    
    def calculate_pnl(
        self,
        price: float,
        side: str
    ) -> Tuple[float, float]:
        """
        Calculate max profit and max loss for a single unit.
        
        Args:
            price: Price of the share (0-1 range, in dollars)
            side: 'YES' or 'NO'
        
        Returns:
            (max_profit, max_loss)
        """
        if side == "YES":
            max_profit = (1 - price) * (1 - self.fee_settlement)
            max_loss = price
        else:
            max_profit = (1 - price) * (1 - self.fee_settlement)
            max_loss = price
        
        return max_profit, max_loss
    
    def calculate_ev(
        self,
        price: float,
        side: str,
        subjective_prob: float
    ) -> float:
        """
        Calculate Expected Value with subjective probability.
        
        Args:
            price: Share price
            side: 'YES' or 'NO'
            subjective_prob: User's belief about probability of YES outcome
        
        Returns:
            Expected value
        """
        if side == "YES":
            ev = (subjective_prob * (1 - price) * (1 - self.fee_settlement) - 
                  (1 - subjective_prob) * price)
        else:
            ev = ((1 - subjective_prob) * (1 - price) * (1 - self.fee_settlement) - 
                  subjective_prob * price)
        
        return ev
    
    def calculate_symmetric_strategy(
        self,
        markets: List[Market],
        anchor: float,
        budget: float,
        bias: float = 0.0,
        risk_cap: Optional[float] = None
    ) -> Tuple[List[OrderRecommendation], PortfolioSummary]:
        """
        Calculate symmetric delta-neutral strategy.
        
        Markets below anchor -> YES (floor)
        Markets above anchor -> NO (ceiling)
        
        Args:
            markets: List of markets with strikes
            anchor: Current spot price (e.g., BTC price)
            budget: Total budget in USD
            bias: -1 to +1, shifts allocation (0 = neutral)
            risk_cap: Optional maximum risk limit
        
        Returns:
            (orders, portfolio_summary)
        """
        markets_with_strikes = [m for m in markets if m.strike and m.strike.K > 0]
        
        if not markets_with_strikes:
            return [], PortfolioSummary(0, 0, 0, 0, 0, 0)
        
        markets_below = [m for m in markets_with_strikes if m.strike and m.strike.K < anchor]
        markets_above = [m for m in markets_with_strikes if m.strike and m.strike.K >= anchor]
        
        alpha = (bias + 1) / 2
        budget_down = (1 - alpha) * budget
        budget_up = alpha * budget
        
        orders = []
        
        if markets_below:
            weights_down = self._calculate_weights(markets_below, anchor, direction="below")
            orders_down = self._allocate_units(
                markets_below, 
                weights_down, 
                budget_down, 
                "YES"
            )
            orders.extend(orders_down)
        
        if markets_above:
            weights_up = self._calculate_weights(markets_above, anchor, direction="above")
            orders_up = self._allocate_units(
                markets_above,
                weights_up,
                budget_up,
                "NO"
            )
            orders.extend(orders_up)
        
        summary = self._create_summary(orders, budget_down, budget_up)
        
        return orders, summary
    
    def _calculate_weights(
        self,
        markets: List[Market],
        anchor: float,
        direction: str
    ) -> Dict[str, float]:
        """
        Calculate exponential weights based on distance from anchor.
        
        Weight = exp(-beta * |K - anchor| / anchor)
        """
        weights = {}
        
        for market in markets:
            if not market.strike:
                continue
            K = market.strike.K
            distance = abs(K - anchor) / anchor
            weight = math.exp(-self.beta * distance)
            weights[market.id] = weight
        
        total_weight = sum(weights.values())
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}
        
        return weights
    
    def _allocate_units(
        self,
        markets: List[Market],
        weights: Dict[str, float],
        budget: float,
        side: str
    ) -> List[OrderRecommendation]:
        """
        Allocate units across markets based on weights and budget.
        """
        orders = []
        
        for market in markets:
            weight = weights.get(market.id, 0)
            
            if weight == 0:
                continue
            
            market_budget = budget * weight
            
            price = market.yes_price if side == "YES" else market.no_price
            
            if price <= 0:
                continue
            
            units_float = market_budget / price
            units = max(1, round(units_float))
            
            actual_cost = units * price
            
            limit_price = min(price + self.slippage_limit, 0.99)
            
            max_profit, max_loss = self.calculate_pnl(price, side)
            
            order = OrderRecommendation(
                market_id=market.id,
                question=market.question,
                strike=market.strike.K if market.strike else None,
                side=side,
                units=units,
                limit_price=limit_price,
                cost=actual_cost,
                max_profit=max_profit * units,
                max_loss=max_loss * units
            )
            
            orders.append(order)
        
        return orders
    
    def _create_summary(
        self,
        orders: List[OrderRecommendation],
        budget_down: float,
        budget_up: float
    ) -> PortfolioSummary:
        """Create portfolio summary from orders"""
        total_cost = sum(o.cost for o in orders)
        max_loss = sum(o.max_loss for o in orders)
        max_profit = sum(o.max_profit for o in orders)
        
        up_cost = sum(o.cost for o in orders if o.side == "NO")
        down_cost = sum(o.cost for o in orders if o.side == "YES")
        
        return PortfolioSummary(
            total_cost=total_cost,
            max_loss=max_loss,
            max_profit=max_profit,
            up_side_cost=up_cost,
            down_side_cost=down_cost,
            num_orders=len(orders)
        )
