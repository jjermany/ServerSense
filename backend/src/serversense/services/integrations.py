from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntegrationDescriptor:
    key: str
    name: str
    description: str
    read_only: bool = True


class IntegrationProvider(ABC):
    descriptor: IntegrationDescriptor

    @abstractmethod
    def test(self, config: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def collect(self, config: dict[str, Any]) -> dict[str, Any]: ...


class IntegrationRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, type[IntegrationProvider]] = {}

    def register(self, provider: type[IntegrationProvider]) -> None:
        key = provider.descriptor.key
        if key in self._providers:
            raise ValueError(f"Integration provider is already registered: {key}")
        self._providers[key] = provider

    def descriptors(self) -> list[IntegrationDescriptor]:
        return [provider.descriptor for provider in self._providers.values()]

    def create(self, key: str) -> IntegrationProvider:
        if key not in self._providers:
            raise ValueError(f"Integration provider is not installed: {key}")
        return self._providers[key]()


registry = IntegrationRegistry()
