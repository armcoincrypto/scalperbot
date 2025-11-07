from abc import ABC, abstractmethod
from typing import Dict, Any
import pandas as pd

class Strategy(ABC):
    def __init__(self, symbol: str, config):
        self.symbol = symbol
        self.config = config

    @abstractmethod
    async def warmup(self, data: Dict[str, pd.DataFrame]):
        pass

    @abstractmethod
    async def generate_signal(self, data: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
        pass
