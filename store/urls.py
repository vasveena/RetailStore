from django.urls import path
from . import views

urlpatterns = [
    path('', views.store, name='store'),
    path('category/<slug:category_slug>/', views.store, name='products_by_category'),
    path('category/<slug:category_slug>/<slug:product_slug>/', views.product_detail, name='product_detail'), 
    path('search/', views.search, name='search'),
    path('submit_review/<int:product_id>/', views.submit_review, name='submit_review'),
    path('generate_description/<int:product_id>/', views.generate_description, name='generate_description'),
    path('save_product_description/<int:product_id>/', views.save_product_description, name='save_product_description'),
    path('generate_product_description/', views.generate_product_description, name='generate_product_description'),
]