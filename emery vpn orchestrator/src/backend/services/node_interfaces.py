from __future__ import annotations

from abc import ABC, abstractmethod

from src.common.models import Device, Subscription, VpnNode


class NodeProvisioningService(ABC):
    @abstractmethod
    def provision_node(self, node: VpnNode) -> dict:
        raise NotImplementedError

    @abstractmethod
    def deprovision_node(self, node: VpnNode) -> dict:
        raise NotImplementedError

    @abstractmethod
    def healthcheck_nodes(self, nodes: list[VpnNode]) -> list[dict]:
        raise NotImplementedError


class NodeConfigService(ABC):
    @abstractmethod
    def build_import_text(self, node: VpnNode, subscription: Subscription, device: Device | None) -> str:
        raise NotImplementedError
