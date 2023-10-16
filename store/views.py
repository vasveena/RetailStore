from django.shortcuts import render, redirect
from .models import Product, ReviewRating, ProductGallery, Variation
from .forms import ReviewForm
from category.models import Category
from django.shortcuts import get_object_or_404
from carts.models import CartItem
from carts.views import _cart_id
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
from django.contrib import messages
from orders.models import OrderProduct
import os
import json
from utils import bedrock, print_ww
from langchain.llms.bedrock import Bedrock
from langchain import PromptTemplate
import warnings

# Create your views here.

boto3_bedrock = bedrock.get_bedrock_client(assumed_role=os.environ.get("BEDROCK_ASSUME_ROLE", None), region=os.environ.get("AWS_DEFAULT_REGION", None))

def store(request, category_slug=None):
    categories = None
    products = None

    if category_slug != None:
       categories = get_object_or_404(Category, slug=category_slug)
       products = Product.objects.filter(category=categories, is_available=True).order_by('id')
       paginator = Paginator(products, 3)
       page = request.GET.get('page')
       paged_products = paginator.get_page(page)
       product_count = products.count()
    else:
        products = Product.objects.all().filter(is_available=True).order_by('id')
        paginator = Paginator(products, 3)
        page = request.GET.get('page')
        paged_products = paginator.get_page(page)
        product_count = products.count()

    context = {
        'products': paged_products,
        'product_count': product_count,
    }
    return render(request, 'store/store.html', context)

def product_detail(request, category_slug, product_slug):
    request.session['generated_flag'] = False
    request.session['product_details'] = None
    request.session['draft_flag'] = False
    request.session.modified = True

    try:
        single_product = Product.objects.get(category__slug=category_slug, slug=product_slug)
        in_cart = CartItem.objects.filter(cart__cart_id=_cart_id(request), product=single_product).exists()
    except Exception as e:
        raise e

    if request.user.is_authenticated:
        try:
            orderproduct = OrderProduct.objects.filter(user=request.user, product_id=single_product.id).exists()
        except OrderProduct.DoesNotExist:
            orderproduct = None
    else:
        orderproduct = None

    # Get reviews
    reviews = ReviewRating.objects.filter(product_id=single_product.id, status=True)

    # Get product gallery
    product_gallery = ProductGallery.objects.filter(product_id=single_product.id)

    context = {
        'single_product': single_product,
        'in_cart'       : in_cart,
        'orderproduct': orderproduct,
        'reviews': reviews,
        'product_gallery': product_gallery,
    }
    #print("user ->" +reviews[0].user.full_name())
    return render(request, 'store/product_detail.html', context)

def search(request):
    if 'keyword' in request.GET:
        keyword = request.GET['keyword']
        if keyword:
            products = Product.objects.order_by('-created_date').filter(Q(description__icontains=keyword) | Q(product_name__icontains=keyword))
            product_count = products.count()
    context = {
        'products': products,
        'product_count': product_count,
    }
    return render(request, 'store/store.html', context)

def submit_review(request, product_id):
    url = request.META.get('HTTP_REFERER')
    if request.method == 'POST':
        try:
            reviews = ReviewRating.objects.get(user__id=request.user.id, product__id=product_id)
            form = ReviewForm(request.POST, instance=reviews)
            form.save()
            messages.success(request, 'Thank you! Your review has been updated.')
            print("in try" +url)
            return redirect(url)
        except ReviewRating.DoesNotExist:
            form = ReviewForm(request.POST)
            if form.is_valid():
                print("form valid")
                data = ReviewRating()
                data.subject = form.cleaned_data['subject']
                data.rating = form.cleaned_data['rating']
                data.review = form.cleaned_data['review']
                data.ip = request.META.get('REMOTE_ADDR')
                data.product_id = product_id
                data.user_id = request.user.id
                print("before save")
                data.save()
                messages.success(request, 'Thank you! Your review has been submitted.')
                return redirect(url)

def generate_description(request, product_id, flag=False):
   try:
        single_product = Product.objects.get(id=product_id)
        product_gallery = ProductGallery.objects.filter(product_id=single_product.id)

   except Exception as e:
        raise e
   
   context = {
        'single_product': single_product,
        'product_gallery': product_gallery,
        'flag': flag,
    }
   return render(request, 'store/generate_description.html', context)

def generate_product_description(request, product_id):
    warnings.filterwarnings('ignore')
    url = request.META.get('HTTP_REFERER')
    single_product = Product.objects.get(id=product_id)
    product_colors = []
    product_vars = Variation.objects.filter(product=single_product, variation_category="color")
    for variation in product_vars:
        product_colors.append(variation.variation_value)
    try:
        product_brand = single_product.product_brand
        product_category = single_product.category
        product_name = single_product.product_name
        product_details = request.GET.get('product_details')
        max_length = request.GET.get('wordrange')

        #Inference parameters for Claude Anthropic
        inference_modifier = {}
        inference_modifier['max_tokens_to_sample'] = int(request.GET.get('max_tokens_to_sample') or 200)
        inference_modifier['temperature'] = float(request.GET.get('temperature') or 0.5)
        inference_modifier['top_k'] = int(request.GET.get('top_k') or 250)
        inference_modifier['top_p'] = float(request.GET.get('top_p') or 1)
        inference_modifier['stop_sequences'] = ["\n\nHuman"]

        textgen_llm = Bedrock(
            model_id="anthropic.claude-instant-v1",
            client=boto3_bedrock,
            model_kwargs=inference_modifier,
        )
        
        # Create a prompt template that has 4 input variables for product brand, color, category and description
        multi_var_prompt = PromptTemplate(
            input_variables=["brand", "colors", "category", "length", "name","details"], 
            template="""
                Human: Create a catchy product description for a {category} from the brand {brand}. Product name is {name}. The number of words should be less than {length}. 
                Following are the product details:  
                <product_details>
                {details}
                </product_details>
                Briefly mention about all the available colors of the product. 
                Example: Available colors are Blue, Purple and Orange. 
                If the <available_colors> is empty, don't mention anything about the color of the product.
                <available_colors>
                {colors}
                </available_colors>

                Assistant:"""
                )

        # Pass in form values to the prompt template
        prompt = multi_var_prompt.format(brand=product_brand, 
                                         colors=product_colors,
                                         category=product_category,
                                         length=max_length,
                                         name=product_name,
                                         details=product_details)
        response = textgen_llm(prompt)

        generated_description = response[response.index('\n')+1:]

    except Exception as e:
        raise e
    request.session['product_details'] = product_details
    request.session['generated_description'] = generated_description
    request.session['prompt'] = prompt
    request.session['generated_flag'] = True
    request.session.modified = True
    return redirect(url)

def save_product_description(request, product_id):
    try:
        single_product = Product.objects.get(id=product_id)
        if 'save_description' in request.POST:
            single_product.description = request.POST.get('generated_description')
            single_product.save()
            success_message = "The product description for " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        elif 'regenerate' in request.POST:
            request.session['generated_flag'] = False
            request.session.modified = True
            return redirect('generate_description', single_product.id)
        else:
            pass
    except:
        pass

def create_response(request, product_id, review_id, draft_flag=False):
    try:
        single_product = Product.objects.get(id=product_id)
        review = ReviewRating.objects.get(product=single_product, id=review_id)

    except Exception as e:
            raise e
    
    context = {
            'single_product': single_product,
            'review': review,
            'draft_flag': draft_flag,
        }
    return render(request, 'store/create_response.html', context)

def create_review_response(request, product_id, review_id):
    url = request.META.get('HTTP_REFERER')
    product = Product.objects.get(id=product_id)
    review = ReviewRating.objects.get(product=product, id=review_id)
    try:
        product_name = product.product_name
        review_text = review.review
        max_length = request.GET.get('wordrange')

        #Inference parameters for Claude Anthropic
        inference_modifier = {}
        inference_modifier['max_tokens_to_sample'] = int(request.GET.get('max_tokens_to_sample') or 200)
        inference_modifier['temperature'] = float(request.GET.get('temperature') or 0.5)
        inference_modifier['top_k'] = int(request.GET.get('top_k') or 250)
        inference_modifier['top_p'] = float(request.GET.get('top_p') or 1)
        inference_modifier['stop_sequences'] = ["\n\nHuman"]

        textgen_llm = Bedrock(
            model_id="anthropic.claude-instant-v1",
            client=boto3_bedrock,
            model_kwargs=inference_modifier,
        )
        
        # Create a prompt template that has 4 input variables for product brand, color, category and description
        multi_var_prompt = PromptTemplate(
            input_variables=["product_name","customer_name","email","phone","length","review"], 
            template="""
                Human: I'm the manager of re:Invent retails. Draft a response for the review of the product {product_name} from our customer {customer_name}. The number of words should be less than {length}. My contact information: {email} {phone}. 
                <customer_review>
                {review}
                <customer_review>

                Example response pattern: 
                Dear <customer_name>,
                <content_body>
                
                <if negative review> 
                    Don't hesitate to contact me at {email} or {phone}. 
                <end if> 

                Sincerely,
                <signature>
                {email}
                {phone}
                Assistant:"""
                )

        # Pass in form values to the prompt template
        prompt = multi_var_prompt.format(product_name=product_name,
                                         customer_name=review.user.full_name(),
                                         email=request.user.email,
                                         phone=request.user.phone_number,
                                         length=max_length,
                                         review=review_text)
        response = textgen_llm(prompt)

        generated_response = response[response.index('\n')+1:]

    except Exception as e:
        raise e

    request.session['generated_response'] = generated_response
    request.session['draft_prompt'] = prompt
    request.session['draft_flag'] = True
    request.session.modified = True

    return redirect(url)

def save_review_response(request, product_id, review_id):
    try:
        request.session['draft_flag'] = False
        single_product = Product.objects.get(id=product_id)
        review = ReviewRating.objects.get(product=single_product, id=review_id)

        if 'save_response' in request.POST:
            review.generated_response = request.POST.get('generated_response')
            review.prompt = request.session.get('draft_prompt')
            review.save()
            success_message = "The response for the review of " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        elif 'regenerate' in request.POST:
            request.session.modified = True
            return redirect('create_response', single_product.id, review.id)
        else:
            pass
    except:
        pass

            
