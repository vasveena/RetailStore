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
            #print("in try" +url)
            return redirect(url)
        except ReviewRating.DoesNotExist:
            form = ReviewForm(request.POST)
            if form.is_valid():
                data = ReviewRating()
                data.subject = form.cleaned_data['subject']
                data.rating = form.cleaned_data['rating']
                data.review = form.cleaned_data['review']
                data.ip = request.META.get('REMOTE_ADDR')
                data.product_id = product_id
                data.user_id = request.user.id
                data.save()
                messages.success(request, 'Thank you! Your review has been submitted.')
                #print("in except" +url)
                return redirect(url)

def generate_description(request, product_id):

#    if 'generated_flag' in request.session:
#        print("poppin")
#        request.session['generated_flag'] = False
#        request.session.modified = True
       
   ex_color_list = []
   try:
        single_product = Product.objects.get(id=product_id)
        product_gallery = ProductGallery.objects.filter(product_id=single_product.id)
        product_vars = Variation.objects.filter(product=single_product, variation_category="color")
        for variation in product_vars:
            ex_color_list.append(variation.variation_value)

   except Exception as e:
        raise e
   
   context = {
        'single_product': single_product,
        'product_gallery': product_gallery,
        'product_colors': ex_color_list,
    }
   return render(request, 'store/generate_description.html', context)

def generate_product_description(request):
    warnings.filterwarnings('ignore')
    url = request.META.get('HTTP_REFERER')
    try:
        product_details = request.GET.get('product_details')
        product_brand = request.GET.get('product_brand')
        product_category = request.GET.get('product_category')
        product_color = request.GET.get('product_color')
        max_length = request.GET.get('wordrange')

        inference_modifier = {
            "max_tokens_to_sample": 4096,
            "temperature": 0.5,
            "top_k": 250,
            "top_p": 1,
            "stop_sequences": ["\n\nHuman"],
        }

        textgen_llm = Bedrock(
            model_id="anthropic.claude-instant-v1",
            client=boto3_bedrock,
            model_kwargs=inference_modifier,
        )
        
        # Create a prompt template that has 4 input variables for product brand, color, category and description
        multi_var_prompt = PromptTemplate(
            input_variables=["brand", "color", "category", "length", "details"], 
            template="""
                Human: Create a catchy product description for a {color} {category} from the brand {brand}. The number of words should be less than {length}. 
                Following are the product details:  
                <product_details>
                {details}
                </product_details>

                Assistant:"""
                )

        # Pass in form values to the prompt template
        prompt = multi_var_prompt.format(brand=product_brand, 
                                         color=product_color,
                                         category=product_category,
                                         length=max_length,
                                         details=product_details)
        response = textgen_llm(prompt)

        generated_description = response[response.index('\n')+1:]

    except Exception as e:
        raise e
    request.session['product_details'] = product_details
    request.session['generated_description'] = generated_description
    request.session['generated_flag'] = True
    request.session['prompt'] = prompt
    request.session.modified = True
    return redirect(url)

def save_product_description(request, product_id):
    try:
        single_product = Product.objects.get(id=product_id)
        if 'save_description' in request.POST:
            print("am here")
            print("generated: " +request.POST.get('generated_description'))
            single_product.description = request.POST.get('generated_description')
            single_product.save()
            success_message = "The product description for " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        elif 'regenerate' in request.POST:
            print("am here")
            request.session['generated_flag'] = False
            request.session.modified = True
            return redirect('generate_description', single_product.id)
        else:
            pass
    except:
        pass

