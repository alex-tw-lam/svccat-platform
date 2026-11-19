"""REST API: the platform's single interface. Every mutation goes through
the provisioning engine (catalog/provisioning.py); this layer validates
input, calls the engine, and shapes the response."""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog import osb, provisioning
from catalog.models import Broker, ServiceBinding, ServiceClass, ServiceInstance, ServicePlan, Tenant
from catalog.serializers import (
    BrokerSerializer,
    CredentialsSerializer,
    ServiceBindingSerializer,
    ServiceClassSerializer,
    ServiceInstanceSerializer,
    ServicePlanSerializer,
    TenantSerializer,
)


class BrokerViewSet(viewsets.ModelViewSet):
    """Register and manage OSB brokers. POST creates; the `sync` action
    fetches the broker's catalog into the local store."""

    queryset = Broker.objects.all()
    serializer_class = BrokerSerializer

    @action(detail=True, methods=["post"])
    def sync(self, request, pk=None):
        """Fetch the broker's /v2/catalog and upsert classes + plans."""
        broker = self.get_object()
        try:
            counts = provisioning.sync_catalog(broker)
        except osb.OSBError as err:
            return Response({"error": str(err)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"synced": True, **counts})


class ServiceClassViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only: classes come from broker catalog syncs."""

    queryset = ServiceClass.objects.prefetch_related("plans").select_related("broker")
    serializer_class = ServiceClassSerializer
    filterset_fields = ["broker", "bindable"]


class ServicePlanViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only: plans come from broker catalog syncs."""

    queryset = ServicePlan.objects.select_related("service_class")
    serializer_class = ServicePlanSerializer
    filterset_fields = ["service_class"]


class TenantViewSet(viewsets.ModelViewSet):
    """Create and manage tenants."""

    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer


class ServiceInstanceViewSet(viewsets.ModelViewSet):
    """The provision lifecycle: POST provisions through the broker,
    DELETE deprovisions. Status transitions are handled by the
    provisioning engine (async polling for 202 responses)."""

    queryset = ServiceInstance.objects.select_related(
        "tenant", "service_class", "service_class__broker", "service_plan"
    )
    serializer_class = ServiceInstanceSerializer
    filterset_fields = ["tenant", "status", "service_class"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(status="provisioning")
        provisioning.provision_instance(instance)
        instance.refresh_from_db()
        return Response(
            self.get_serializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        """PATCH updates the plan or parameters (an OSB update call)."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(status="updating")
        provisioning.provision_instance(instance)  # same OSB PUT, broker handles update
        instance.refresh_from_db()
        return Response(self.get_serializer(instance).data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        ok = provisioning.deprovision_instance(instance)
        if ok:
            instance.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            {"error": instance.error_message},
            status=status.HTTP_502_BAD_GATEWAY,
        )


class ServiceBindingViewSet(viewsets.ModelViewSet):
    """The bind lifecycle: POST binds through the broker and stores the
    credentials encrypted; the `credentials` action reveals them."""

    queryset = ServiceBinding.objects.select_related(
        "tenant", "service_instance", "service_instance__service_class__broker"
    )
    serializer_class = ServiceBindingSerializer
    filterset_fields = ["tenant", "status", "service_instance"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        binding = serializer.save(status="creating")
        try:
            provisioning.create_binding(binding)
        except osb.OSBError as err:
            return Response(
                {"error": str(err)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        binding.refresh_from_db()
        return Response(
            self.get_serializer(binding).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        binding = self.get_object()
        ok = provisioning.delete_binding(binding)
        if ok:
            binding.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            {"error": "unbind failed"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    @action(detail=True, methods=["get"])
    def credentials(self, request, pk=None):
        """Reveal the binding's stored credentials (decrypted)."""
        binding = self.get_object()
        if binding.status != "ready":
            return Response(
                {"error": "credentials are not ready yet"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"credentials": binding.credentials})
