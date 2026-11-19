from django.urls import include, path
from rest_framework.routers import DefaultRouter

from catalog import api

router = DefaultRouter()
router.register(r"brokers", api.BrokerViewSet)
router.register(r"classes", api.ServiceClassViewSet)
router.register(r"plans", api.ServicePlanViewSet)
router.register(r"tenants", api.TenantViewSet)
router.register(r"instances", api.ServiceInstanceViewSet)
router.register(r"bindings", api.ServiceBindingViewSet)

urlpatterns = [
    path("api/v1/", include(router.urls)),
]
