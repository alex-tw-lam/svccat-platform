"""DRF serializers: the API surface between the internal models and the
JSON the client sees. Read-only fields (status, credentials) are managed
by the provisioning engine, never writable by the API consumer."""

from rest_framework import serializers

from catalog.models import Broker, ServiceBinding, ServiceClass, ServiceInstance, ServicePlan, Tenant


class BrokerSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)  # accepted on input, never returned

    class Meta:
        model = Broker
        fields = ["id", "name", "url", "username", "password", "created_at"]
        read_only_fields = ["id", "created_at"]


class ServicePlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServicePlan
        fields = [
            "id", "external_id", "name", "description",
            "free", "bindable", "metadata", "created_at",
        ]
        read_only_fields = ["id", "external_id", "created_at"]


class ServiceClassSerializer(serializers.ModelSerializer):
    plans = ServicePlanSerializer(many=True, read_only=True)
    broker_name = serializers.CharField(source="broker.name", read_only=True)

    class Meta:
        model = ServiceClass
        fields = [
            "id", "external_id", "broker_name", "name", "description",
            "bindable", "tags", "metadata", "plans", "created_at",
        ]
        read_only_fields = ["id", "external_id", "created_at"]


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "display_name", "created_at"]
        read_only_fields = ["id", "created_at"]


class ServiceInstanceSerializer(serializers.ModelSerializer):
    service_class_name = serializers.CharField(source="service_class.name", read_only=True)
    service_plan_name = serializers.CharField(source="service_plan.name", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)

    class Meta:
        model = ServiceInstance
        fields = [
            "id", "tenant", "tenant_name", "service_class", "service_class_name",
            "service_plan", "service_plan_name", "name", "parameters",
            "status", "dashboard_url", "error_message",
            "broker_instance_id", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "dashboard_url", "error_message",
            "broker_instance_id", "created_at", "updated_at",
        ]


class ServiceBindingSerializer(serializers.ModelSerializer):
    instance_name = serializers.CharField(source="service_instance.name", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)

    class Meta:
        model = ServiceBinding
        fields = [
            "id", "tenant", "tenant_name", "service_instance", "instance_name",
            "name", "parameters", "status",
            "broker_binding_id", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "broker_binding_id", "created_at", "updated_at",
        ]


class CredentialsSerializer(serializers.Serializer):
    """The credential reveal — read-only, from the encrypted store."""
    credentials = serializers.SerializerMethodField()

    def get_credentials(self, obj):
        return obj.credentials
