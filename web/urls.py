from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),

    # Buyer portal ----------------------------------------------------
    path("buyer/", views.buyer_dashboard, name="buyer_dashboard"),
    path("buyer/tenders/", views.tender_list, name="tender_list"),
    path("buyer/tenders/create/", views.create_tender, name="create_tender"),
    path("buyer/tenders/<str:tender_id>/", views.tender_detail, name="tender_detail"),
    path("buyer/tenders/<str:tender_id>/process/", views.process_tender, name="process_tender"),
    path(
        "buyer/tenders/<str:tender_id>/documents/upload/",
        views.upload_tender_document,
        name="upload_tender_document",
    ),
    path(
        "buyer/tenders/<str:tender_id>/documents/<str:filename>/",
        views.document_viewer,
        name="tender_document_viewer",
    ),
    path(
        "buyer/tenders/<str:tender_id>/requirements/<int:requirement_id>/",
        views.requirement_detail,
        name="requirement_detail",
    ),
    path(
        "buyer/tenders/<str:tender_id>/bids/<str:bid_id>/",
        views.bid_detail,
        name="bid_detail",
    ),
    path(
        "buyer/tenders/<str:tender_id>/bids/<str:bid_id>/process/",
        views.process_bid,
        name="process_bid",
    ),
    path(
        "buyer/tenders/<str:tender_id>/bids/<str:bid_id>/documents/<str:filename>/",
        views.document_viewer,
        name="bid_document_viewer",
    ),
    path(
        "buyer/tenders/<str:tender_id>/bids/<str:bid_id>/compliance/<int:requirement_id>/",
        views.compliance_detail,
        name="compliance_detail",
    ),

    # Seller portal ----------------------------------------------------
    path("seller/", views.seller_dashboard, name="seller_dashboard"),
    path("seller/tenders/<str:tender_id>/", views.seller_tender_detail, name="seller_tender_detail"),
    path(
        "seller/tenders/<str:tender_id>/bids/create/",
        views.seller_create_bid,
        name="seller_create_bid",
    ),
    path(
        "seller/tenders/<str:tender_id>/bids/<str:bid_id>/",
        views.seller_bid_detail,
        name="seller_bid_detail",
    ),
    path(
        "seller/tenders/<str:tender_id>/bids/<str:bid_id>/documents/upload/",
        views.seller_upload_bid_document,
        name="seller_upload_bid_document",
    ),
    path(
        "seller/tenders/<str:tender_id>/bids/<str:bid_id>/process/",
        views.seller_process_bid,
        name="seller_process_bid",
    ),
]
