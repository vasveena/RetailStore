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
from langchain.embeddings import BedrockEmbeddings
from langchain import PromptTemplate
import warnings
from PIL import Image
import base64
import io
import json
import random
from decouple import config
import boto3
import string
import numpy as np
import requests
import psycopg2
from pgvector.psycopg2 import register_vector

# Initialize Bedrock client 
boto3_bedrock = bedrock.get_bedrock_client(assumed_role=os.environ.get("BEDROCK_ASSUME_ROLE", None), region=os.environ.get("AWS_DEFAULT_REGION", None))

# Initialize S3 client
s3 = boto3.client('s3', region_name=os.environ.get("AWS_DEFAULT_REGION", None))

# Initialize secrets manager
secrets = boto3.client('secretsmanager', region_name=os.environ.get("AWS_DEFAULT_REGION", None))

# Create your views here.

####################### START SECTION - OTHER WEB APPLICATION FEATURES ##########################

## This section contains functions that will be used for other functionalities of our retail web application.
## For example, submitting user review or displaying products in catalog.

## This section can be safely ignored
## Please don't modify anything in this section

def store(request, category_slug=None):
    categories = None
    products = None

    if category_slug != None:
       categories = get_object_or_404(Category, slug=category_slug)
       products = Product.objects.filter(category=categories, is_available=True).order_by('category')
       paginator = Paginator(products, 6)
       page = request.GET.get('page')
       paged_products = paginator.get_page(page)
       product_count = products.count()
    else:
        products = Product.objects.all().filter(is_available=True).order_by('category')
        paginator = Paginator(products, 6)
        page = request.GET.get('page')
        paged_products = paginator.get_page(page)
        product_count = products.count()

    context = {
        'products': paged_products,
        'product_count': product_count,
    }
    return render(request, 'store/store.html', context)

def product_detail(request, category_slug, product_slug):
    request.session['product_description_flag'] = False
    request.session['product_details'] = None
    request.session['draft_flag'] = False
    request.session['summary_flag'] = False
    request.session['image_flag'] = False
    request.session['change_prompt'] = None
    request.session['negative_prompt'] = None
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
    first_name=request.POST.get('first_name')
    last_name=request.POST.get('last_name')
    if request.method == 'POST':
        try:
            reviews = ReviewRating.objects.get(first_name=first_name, last_name=last_name, product__id=product_id)
            form = ReviewForm(request.POST, instance=reviews)
            form.save()
            messages.success(request, 'Thank you! Your review has been updated.')
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
                #data.user_id = request.user.id
                data.first_name = first_name
                data.last_name = last_name
                data.save()
                messages.success(request, 'Thank you! Your review has been submitted.')
                return redirect(url)

####################### END SECTION - OTHER WEB APPLICATION FEATURES ##########################

####################### START SECTION - HANDLER FUNCTIONS GENAI FEATURES ##########################

## Functions in this section will be used to: 

# 1. Render HTML pages needed for the GenAI features we are going to implement
# 2. Save LLM response to web application database
# 3. Any andler functions used for manipulating input fed to the LLM 

## This section can be safely ignored
## Please don't modify anything in this section

#### HANDLER FUNCTIONS FOR GENERATING PRODUCT DESCRIPTION FEATURE ####

# This function is used to just render HTML page for generate product description functionality
def generate_description(request, product_id):
   try:
        # get product from product ID 
        single_product = Product.objects.get(id=product_id)

   except Exception as e:
        raise e
   
   # pass product object to context (to be used in generate_description.html)
   context = {
        'single_product': single_product,
    }
   
   # render HTML page generate_description.html
   return render(request, 'store/generate_description.html', context)

#This function is used for saving product description to database
def save_product_description(request, product_id):
    try:
        single_product = Product.objects.get(id=product_id)

        # If user input is to save description
        if 'save_description' in request.POST:
            single_product.description = request.POST.get('generated_description')
            single_product.save()
            success_message = "The product description for " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        # If user input is to regenerate
        elif 'regenerate' in request.POST:
            request.session['product_description_flag'] = False
            request.session.modified = True
            return redirect('generate_description', single_product.id)
        else:
            # do nothing
            pass
    except:
        pass

#### HANDLER FUNCTIONS FOR DRAFTING RESPONSE TO CUSTOMER REVIEW FEATURE ####

# This function is used to just render HTML page for create response to customer review functionality
def create_response(request, product_id, review_id):
    try:
        # get product from product ID
        single_product = Product.objects.get(id=product_id)
        # get single customer review using product ID, review ID
        review = ReviewRating.objects.get(product=single_product, id=review_id)

    except Exception as e:
            raise e
    
    # pass objects to context (to be used in create_response.html)
    context = {
            'single_product': single_product,
            'review': review,
        }
    # render HTML page create_response.html
    return render(request, 'store/create_response.html', context)

# This function is used for saving customer review response to database
def save_review_response(request, product_id, review_id):
    try:
        # get single product review using product ID and review ID 
        request.session['draft_flag'] = False
        single_product = Product.objects.get(id=product_id)
        review = ReviewRating.objects.get(product=single_product, id=review_id)

        # If user input is to save response
        if 'save_response' in request.POST:
            review.generated_response = request.POST.get('generated_response')
            review.prompt = request.session.get('draft_prompt')
            review.save()
            success_message = "The response for the review of " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        
        # If user input is to regenerate review response
        elif 'regenerate' in request.POST:
            request.session.modified = True
            return redirect('create_response', single_product.id, review.id)
        else:
            # do nothing
            pass
    except:
        pass

#### HANDLER FUNCTIONS FOR SUMMARIZING CUSTOMER REVIEWS FEATURE ####

# This function is used to just render HTML page for summarize customer reviews functionality
def generate_summary(request, product_id): 
    try:
        # get product from product ID
        single_product = Product.objects.get(id=product_id)
        # get all customer reviews for this product
        product_reviews = ReviewRating.objects.filter(product=single_product, status=True)

    except Exception as e:
            raise e
    
    # pass objects to context (to be used in generate_summary.html)
    context = {
            'single_product': single_product,
            'reviews': product_reviews,
        }
    
    # render HTML page generate_summary.html
    return render(request, 'store/generate_summary.html', context)

# This function is used for saving summarized customer reviews to database
def save_summary(request, product_id):
    try:
        # get single product review using product ID and review ID 
        single_product = Product.objects.get(id=product_id)
        request.session['summary_flag'] = False

        # If user input is to save review summary
        if 'save_summary' in request.POST:
            single_product.review_summary = request.session['generated_summary']
            single_product.save()
            success_message = "The summary for the review of " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        # If user input is to regenerate review summary
        elif 'regenerate' in request.POST:
            request.session.modified = True
            return redirect('generate_summary', single_product.id)
        else:
            # do nothing
            pass
    except:
        pass

#### HANDLER FUNCTIONS FOR CREATING NEW DESIGN IDEAS FEATURE ####

# This function is used to just render the HTML page studio.html
def design_studio(request, product_id):
    single_product = Product.objects.get(id=product_id)
    context = {
        'single_product': single_product,
    }
    return render(request, 'store/studio.html', context)

# Handy function to convert an image to base64 string
# Stabile Diffusion LLM expects the input image to be in base64 string format
def image_to_base64(img) -> str:
    """Convert a PIL Image or local image file path to a base64 string for Amazon Bedrock"""
    if isinstance(img, str):
        if os.path.isfile(img):
            with open(img, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        else:
            raise FileNotFoundError(f"File {img} does not exist")
    elif isinstance(img, Image.Image):
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    else:
        raise ValueError(f"Expected str (filename) or PIL Image. Got {type(img)}")

#### HANDLER FUNCTIONS FOR QUESTION ANSWERING FEATURE ####

# This function is used for extracting string within a tag. For example, get string embedded within <query></query>
def extract_strings_recursive(test_str, tag):
    # finding the index of the first occurrence of the opening tag
    start_idx = test_str.find("<" + tag + ">")
 
    # base case
    if start_idx == -1:
        return []
 
    # extracting the string between the opening and closing tags
    end_idx = test_str.find("</" + tag + ">", start_idx)
    res = [test_str[start_idx+len(tag)+2:end_idx]]
 
    # recursive call to extract strings after the current tag
    res += extract_strings_recursive(test_str[end_idx+len(tag)+3:], tag)
 
    return res

####################### END SECTION - HANDLER FUNCTIONS GENAI FEATURES ##########################

####################### START SECTION - IMPLEMENT GENAI FEATURES FOR WORKSHOP ##########################

#### This is the only section where you will add functions needed for implementing GenAI features into your retail application
#### Please don't edit any sections other than this one. 

#### FEATURE 1 - GENERATE PRODUCT DESCRIPTION ####

# This function is used for generating product description using LLM from Bedrock
def generate_product_description(request, product_id):
    
    # get current URL for redirecting
    url = request.META.get('HTTP_REFERER')
    # get product from product ID
    single_product = Product.objects.get(id=product_id)
    product_colors = []
    # get product colors
    product_vars = Variation.objects.filter(product=single_product, variation_category="color")
    for variation in product_vars:
        product_colors.append(variation.variation_value)
    try:
        # get product name, brand, category, color
        # get product details from user input from the web application form
        # these data will be used to construct the prompt which will be passed to the LLM to generate product description. 
        product_brand = single_product.product_brand
        product_category = single_product.category
        product_name = single_product.product_name
        product_details = request.GET.get('product_details')
        max_length = request.GET.get('wordrange')

        # get inference parameters from form for Claude Anthropic
        inference_modifier = {}
        inference_modifier['max_tokens_to_sample'] = int(request.GET.get('max_tokens_to_sample') or 200)
        inference_modifier['temperature'] = float(request.GET.get('temperature') or 0.5)
        inference_modifier['top_k'] = int(request.GET.get('top_k') or 250)
        inference_modifier['top_p'] = float(request.GET.get('top_p') or 1)
        inference_modifier['stop_sequences'] = ["\n\nHuman"]

        # Initialize LLM
        textgen_llm = Bedrock(
            model_id="anthropic.claude-instant-v1",
            client=boto3_bedrock,
            model_kwargs=inference_modifier,
        )
        
        # Create a prompt template that has 6 input variables: 
        # product name
        # product brand
        # product color
        # product category (shirt, jeans etc.)
        # product details obtained from user input, and max length of the description requested from LLM. 

        prompt_template = PromptTemplate(
            input_variables=["brand", "colors", "category", "length", "name","details"], 
            template="""
                    Human: Create a catchy product description for a {category} from the brand {brand}. 
                    Product name is {name}. 
                    The number of words should be less than {length}. 
                    
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

                    Assistant:

                    """
                )

        # pass in the variables to the prompt template
        prompt = prompt_template.format(brand=product_brand, 
                                         colors=product_colors,
                                         category=product_category,
                                         length=max_length,
                                         name=product_name,
                                         details=product_details)
        
        # generate product description from Bedrock with the constructed prompt
        response = textgen_llm(prompt)

        # get the second paragraph i.e, only the product description 
        generated_description = response[response.index('\n')+1:]

    except Exception as e:
        raise e
    
    # set session parameters to use in HTML template 
    request.session['product_details'] = product_details
    request.session['generated_description'] = generated_description
    request.session['prompt'] = prompt
    request.session['product_description_flag'] = True
    request.session.modified = True

    # redirect to the previous URL (i.e., generate_description.html). 
    # From there, user can either save description or regenerate it. 
    return redirect(url)

#### FEATURE 2 - DRAFTING RESPONSE TO CUSTOMER REVIEWS ####

# This function is used for drafting response to customer reviews using LLM from Bedrock
def create_review_response(request, product_id, review_id):
    # get current URL for redirecting
    url = request.META.get('HTTP_REFERER')
    # get product from product ID
    product = Product.objects.get(id=product_id)
    # get single customer review using product ID, review ID
    review = ReviewRating.objects.get(product=product, id=review_id)
    try:
        # Get product name, customer review 
        # and number of words to generate as a response to customer review
        # these data will be used to construct the prompt which will be passed to the LLM to create response to customer review. 
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

        # Initialize LLM
        textgen_llm = Bedrock(
            model_id="anthropic.claude-instant-v1",
            client=boto3_bedrock,
            model_kwargs=inference_modifier,
        )
        
        # Create a prompt template that has 4 input variables for product brand, color, category and description
        prompt_template = PromptTemplate(
            input_variables=["product_name","customer_name","manager_name","email","phone","length","review"], 
            template="""
                    Human: 
                    
                    I'm the manager of re:Invent retails. 
                    
                    Draft a response for the review of the product {product_name} from our customer {customer_name}. 
                    The number of words should be less than {length}. 
                    
                    My contact information is email: {email}, phone: {phone}.
                    
                    <customer_review>
                        {review}
                    <customer_review>

                    <example_response_pattern>
                    
                        Dear <customer_name>,
                        <content_body>

                        <if negative review> 
                            Don't hesitate to reach out to me at {phone}.
                        <end if> 

                        Sincerely,
                        {manager_name}
                        <signature>
                        {email}
                    
                    </example_response_pattern>
                    
                    Assistant:
                    
                    """
                )

        # Pass in form values to the prompt template
        prompt = prompt_template.format(product_name=product_name,
                                         customer_name=review.first_name,
                                         manager_name=request.user.full_name(),
                                         email=request.user.email,
                                         phone=request.user.phone_number,
                                         length=max_length,
                                         review=review_text)
        
        # Generate response to customer review using prompt constructed above
        response = textgen_llm(prompt)

        # Get the second paragraph i.e, only the response to customer review
        generated_response = response[response.index('\n')+1:]

    except Exception as e:
        raise e

    # set session parameters to use in HTML template
    request.session['generated_response'] = generated_response
    request.session['draft_prompt'] = prompt
    request.session['draft_flag'] = True
    request.session.modified = True

    # redirect to the previous URL (i.e., create_response.html). 
    # From there, user can either save review response or regenerate it.
    return redirect(url)

#### FEATURE 3 - CREATE NEW DESIGN IDEAS FROM PRODUCT ####

# This function is used for creating design ideas (images) for a product
def create_design_ideas(request, product_id):
    # get current URL for redirecting
    url = request.META.get('HTTP_REFERER')
    # get product from product ID
    single_product = Product.objects.get(id=product_id)
    # get S3 bucket name from config file. this bucket was created as a part of the workshop. 
    bucket_name = config('AWS_STORAGE_BUCKET_NAME')
    
    try:
        # if user chose to delete previously generated image from Stable Diffusion model
        if 'delete_previous' in request.GET:
            # delete previously generated image  
            print("delete image path: " +request.session['image_file_path'])
            product_gallery_del = ProductGallery.objects.filter(product=single_product, image=request.session['image_file_path'])
            if product_gallery_del:
                s3.delete_object(Bucket=bucket_name, Key=request.session['image_file_path'])
                product_gallery_del.delete()
                del request.session['image_flag']
            return redirect('create_design_ideas', single_product.id)
        
        # IF user chose to delete all generated images from Stable Diffusion model
        if 'delete_all' in request.GET:
            # delete existing image gallery 
            del request.session['image_flag']
            product_gallery_del = ProductGallery.objects.filter(product=single_product)
            if product_gallery_del:
                for x in product_gallery_del:
                    key = x.image.url.split(".com/",1)[1]
                    s3.delete_object(Bucket=bucket_name, Key=key)
                product_gallery_del.delete()
            return redirect('create_design_ideas', single_product.id)
        
        # Open product image
        image = Image.open(single_product.images)
        # Resize product image to 512x512 for Stable Diffusion
        resize = image.resize((512,512))

        # Get inference parameters from web application form

        # This prompt is used to generate new ideas from the existing image
        change_prompt = request.GET.get('change_prompt')

        # Negative prompts that will be given -1.0 weight while generating new image
        negprompts = request.GET.get('negative_prompt')
        negative_prompts = []
        for negprompt in negprompts.split('\n'):
            negative_prompts.append(negprompt.replace('\r',''))
        
        # Other Stable Diffusion parameters
        start_schedule = float(request.GET.get('start_schedule')) or 0.5
        steps = int(request.GET.get('steps')) or 30
        cfg_scale = int(request.GET.get('cfg_scale')) or 10
        image_strength = float(request.GET.get('image_strength')) or 0.5
        denoising_strength = float(request.GET.get('denoising_strength')) or 0.5
        seed = int(request.GET.get('seed')) or random.randint(1, 1000000)
        style_preset = request.GET.get('style_preset') or "photographic"

        # Convert image to base64 string
        init_image_b64 = image_to_base64(resize)

        # Construct request body for Stable Diffusion model
        sd_request = json.dumps({
                    "text_prompts": (
                        [{"text": change_prompt, "weight": 1.0}]
                        + [{"text": negprompt, "weight": -1.0} for negprompt in negative_prompts]
                    ),
                    "cfg_scale": cfg_scale,
                    "init_image": init_image_b64,
                    "seed": seed,
                    "start_schedule": start_schedule,
                    "steps": steps,
                    "style_preset": style_preset,
                    "image_strength":image_strength,
                    "denoising_strength": denoising_strength
                })
        
        # Invoke Stable Diffusion model
        response = boto3_bedrock.invoke_model(body=sd_request, modelId="stability.stable-diffusion-xl")

        # Extract image from response body
        response_body = json.loads(response.get("body").read())
        genimage_b64_str = response_body["artifacts"][0].get("base64")
        genimage = Image.open(io.BytesIO(base64.decodebytes(bytes(genimage_b64_str, "utf-8"))))
        
        # Save the image to an in-memory file
        in_mem_file = io.BytesIO()
        genimage.save(in_mem_file, format="PNG")
        in_mem_file.seek(0)

        # Upload image to static s3 path
        image_file_path = single_product.slug + "_generated" + ''.join(random.choices(string.ascii_lowercase, k=5)) + ".png"
    
        s3.upload_fileobj(
            in_mem_file, # image
            bucket_name,
            'media/store/products/' + image_file_path
        )

        # Save generated image to database
        product_gallery = ProductGallery()
        product_gallery.product = single_product
        product_gallery.image = 'store/products/' + image_file_path
        product_gallery.save()

        # Set session parameters to use in HTML template
        request.session['change_prompt'] = change_prompt
        request.session['negative_prompt'] = negprompts
        request.session['image_file_path'] = 'store/products/' + image_file_path
        request.session['image_flag'] = True
        request.session['image_url'] = product_gallery.image.url
        request.session.modified = True

        # Signal success message to user
        messages.success(request, "Design idea saved!")
            
    except Exception as e: 
        print(e)
        
    # redirect to the previous URL (i.e., studio.html).
    return redirect(url)


#### FEATURE 4 - SUMMARIZE CUSTOMER REVIEWS FOR A PRODUCT ####

# This function is used for summarizing customer reviews using LLM from Bedrock
def generate_review_summary(request, product_id):
    # get current URL for redirecting
    url = request.META.get('HTTP_REFERER')
    # get product from product ID
    single_product = Product.objects.get(id=product_id)
    # get all customer reviews for this product
    product_reviews = ReviewRating.objects.filter(product=single_product)

    # get a list of customer reviews for this product and enclose them in <review></review> tags
    # this will be used in the prompt template for summarizing customer reviews
    # doing it this way helps LLM understand our instruction better
    review_digest = ''

    for review in product_reviews:
        review_digest += "<review>" + '\n'
        review_digest += review.review + '\n'
        review_digest += "</review>" + '\n\n'

    try:
        # If user chose Claude
        if 'Claude' in request.GET.get('llm'):
            #Inference parameters for Claude Anthropic
            inference_modifier = {}
            inference_modifier['max_tokens_to_sample'] = int(request.GET.get('claude_max_tokens_to_sample') or 200)
            inference_modifier['temperature'] = float(request.GET.get('claude_temperature') or 0.5)
            inference_modifier['top_k'] = int(request.GET.get('claude_top_k') or 250)
            inference_modifier['top_p'] = float(request.GET.get('claude_top_p') or 1)
            inference_modifier['stop_sequences'] = ["\n\nHuman"]

            # Initialize Claude LLM
            textgen_llm = Bedrock(
                model_id="anthropic.claude-instant-v1",
                client=boto3_bedrock,
                model_kwargs=inference_modifier,
            )
        
         # If user chose Titan
        elif 'Titan' in request.GET.get('llm'):
            #Inference parameters for Titan
            inference_modifier = {}
            inference_modifier['maxTokenCount'] = int(request.GET.get('titan_max_tokens_to_sample') or 200)
            inference_modifier['temperature'] = float(request.GET.get('titan_temperature') or 0.5)
            inference_modifier['topP'] = int(request.GET.get('titan_top_p') or 250)

            # Initialize Titan LLM
            textgen_llm = Bedrock(
                model_id="amazon.titan-tg1-large",
                client=boto3_bedrock,
                model_kwargs=inference_modifier,
                )
            
        else:
            pass

        # Create prompt for summarizing customer reviews. Passing product name and all the customer reviews as parameters to the prompt template. 
        prompt_template = PromptTemplate(
            input_variables=["product_name","reviews"],
            template="""

                Human: Provide a review summary including pros and cons based on the customer reviews for the product {product_name}. This summary will be updated in the product webpage. Customer reviews are enclosed in <customer_reviews> tag. 
        
                <customer_reviews>
                    {reviews}
                <customer_reviews>
                
                Assistant:

                """
            )
        
        # Pass in values to the prompt template
        prompt = prompt_template.format(product_name=single_product.product_name,
                                         reviews=review_digest)

        # Generate review summary using prompt constructed above
        response = textgen_llm(prompt)

        # Set session parameters to use in HTML template
        request.session['generated_summary'] = response
        request.session['summary_prompt'] = prompt
        request.session['summary_flag'] = True
        request.session.modified = True

    except: 
        pass

    # redirect to the previous URL (i.e., generate_summary.html).
    return redirect(url)


#### FEATURE 5 - QUESTION ANSWERING WITH SQL GENERATION ####

# This function is used for answering user questions in natural language using SQL generation and result interpretation by LLM
def ask_question(request):
    # initialize variables
    context={}
    is_query_generated = False
    describe_query_result = ''

    if 'question' in request.GET:
        # get user question from web application 
        question = request.GET.get('question')

        # read Postgres schema file stored in S3
        s3 = boto3.client('s3')
        resp = s3.get_object(Bucket=config('AWS_STORAGE_BUCKET_NAME'), Key="data/schema-postgres.sql")
        schema = resp['Body'].read().decode("utf-8")

        # Prompt template for LLM
        # This prompt template will generate an SQL query based on the schema passed above. 
        # We are passing PostgresQL documentation to help with the SQL generation. 
        # Generated query will be embedded in <query></query> tags
        prompt_template = """
            Human: Create an Postgres SQL query for a retail website to answer the question keeping the following rules in mind: 
            
            1. Database is implemented in Postgres SQL.
            2. Postgres syntax details can be found here: https://www.postgresql.org/files/documentation/pdf/15/postgresql-15-US.pdf
            3. Enclose the query in <query></query>. 
            4. Use "like" and upper() for string comparison on both left hand side and right hand side of the expression. For example, if the query contains "jackets", use "where upper(product_name) like upper('%jacket%')". 
            5. If the question is generic, like "where is mount everest" or "who went to the moon first", then do not generate any query in <query></query> and do not answer the question in any form. Instead, mention that the answer is not found in context.
            6. If the question is not related to the schema, then do not generate any query in <query></query> and do not answer the question in any form. Instead, mention that the answer is not found in context.  

            <schema>
                {schema}
            </schema>

            Question: {question}

            Assistant:
            
            """

        # Prompt template variables
        prompt_vars = PromptTemplate(template=prompt_template, input_variables=["question","schema"])
            
        # Initialize LLM
        llm = Bedrock(model_id="anthropic.claude-instant-v1", client=boto3_bedrock)
        
        # Pass question and postgres schema of the web application
        prompt = prompt_vars.format(question=question, schema=schema)

        try: 
            # Invoke LLM and get response
            llm_response = llm(prompt)

            # Check if query is generated under <query></query> tags as instructed in our prompt
            if "<query>".upper() not in llm_response.upper():
                print("no query generated")
                is_query_generated  = False
                describe_query_result = llm_response
                resultset=''
                query=''
            
            else: 
                is_query_generated = True
                # Extract the query from the response
                query = extract_strings_recursive(llm_response, "query")[0]
                print("Query generated by LLM: " +query)

                # Get database connection details from Secrets Manager
                response = secrets.get_secret_value(SecretId=config('AWS_DATABASE_SECRET_ID'))
                database_secrets = json.loads(response['SecretString'])

                # Connect to PostgreSQL database
                dbhost = database_secrets['host']
                dbport = database_secrets['port']
                dbuser = database_secrets['username']
                dbpass = database_secrets['password']
                dbname = database_secrets['name']

                dbconn = psycopg2.connect(host=dbhost, user=dbuser, password=dbpass, port=dbport, database=dbname, connect_timeout=10)
                dbconn.set_session(autocommit=True)
                cursor = dbconn.cursor()

                # Execute the extracted query
                cursor.execute(query)
                query_result = cursor.fetchall()

                # Close database connection
                cursor.close()
                dbconn.close()
                
                # get query result
                resultset = ''
                if len(query_result) > 0:
                    for x in query_result:
                        resultset = resultset + ''.join(str(x)) + "\n"

                print("Query result: \n" +resultset)

                # Prompt template for LLM
                # This prompt template defines rules while describing query result. 
                # This is the final result that will be seen by the user as an answer to their question. 
                # Idea is to derive natural language answer for a natural language question. 
                prompt_template = """

                Human: This is a Q&A application. We need to answer questions asked by the customer at an e-commerce store. 
                The question asked by the customer is {question}
                We ran an SQL query in our database to get the following result. 

                <resultset>
                {resultset}
                </resultset>

                Summarize the above result and answer the question asked by the customer keeping the following rules in mind: 
                1. Don't make up answers if <resultset></resultset> is empty or none. Instead, answer that the item is not available based on the question.
                2. Mask the PIIs phone, email and address if found the answer with "<PII masked>"
                3. Don't say "based on the output" or "based on the query" or "based on the question" or something similar.  
                4. Keep the answer concise. 
                5. Don't give an impression to the customer that a query was run. Instead, answer naturally. 

                Assistant:

                """

                # Pass user question and query result to prompt template
                prompt_vars = PromptTemplate(template=prompt_template, input_variables=["question","resultset"])

                prompt = prompt_vars.format(question=question, resultset=resultset)

                # Invoke LLM and get response
                describe_query_result = llm(prompt)
                print("describe_query_result " + describe_query_result)

                # If length of response is 0, then set response to "Sorry, I could not answer that question."
                if len(describe_query_result) == 0:
                    describe_query_result = "Sorry, I could not answer that question."

        except:
            query = "Sorry, I could not answer that question."

        # Set context variables for HTML template
        context = {
            "question": question,
            "query": query,
            "is_query_generated": is_query_generated,
            "describe_query_result": describe_query_result,
        }

    # Render HTML template
    return render(request, 'store/question.html', context)

#### FEATURE 6 - VECTOR SEARCH ####

# This function is used for searching similar products using vector embeddings
def vector_search(request):
    if 'keyword' in request.GET:
        # Get search keyword from user 
        keyword = request.GET['keyword']
        if keyword:
            # Initialize Titan embeddings model
            bedrock_embeddings = BedrockEmbeddings(model_id="amazon.titan-embed-g1-text-02", client=boto3_bedrock)

            # Generate vector embeddings for the search keyword
            search_embedding = list(bedrock_embeddings.embed_query(keyword))

            # Get database connection details from Secrets Manager
            response = secrets.get_secret_value(SecretId=config('AWS_DATABASE_SECRET_ID'))
            database_secrets = json.loads(response['SecretString'])

            # Connect to PostgreSQL database
            dbhost = database_secrets['host']
            dbport = database_secrets['port']
            dbuser = database_secrets['username']
            dbpass = database_secrets['password']
            dbname = database_secrets['vectorDbIdentifier']

            dbconn = psycopg2.connect(host=dbhost, user=dbuser, password=dbpass, port=dbport, database=dbname, connect_timeout=10)
            dbconn.set_session(autocommit=True)

            # Register vector db
            register_vector(dbconn)
            cur = dbconn.cursor()

            # Search similar products using vector embeddings
            # Please note that in order to save time, all the 8500+ vector embeddings are pre-populated into your Amazon RDS database instance 
            # using pgvector extension
            cur.execute("""SELECT id, url, description, descriptions_embeddings 
                        FROM vector_products
                        ORDER BY descriptions_embeddings <-> %s limit 10;""", 
                        (np.array(search_embedding),))

            # Get search results
            r = cur.fetchall()
            product_count = len(r)

            # Print similarity search results to web application
            combined = []
            for x in r:
                c = {}
                url = x[1].split('?')[0]
                product_item_id = x[0]
                desc = x[2]
                response = requests.get(url)
                img = Image.open(io.BytesIO(response.content))
                img = img.resize((256, 256))
                buf = io.BytesIO()
                img.save(buf, 'jpeg')
                image_bytes = buf.getvalue()
                encoded = base64.b64encode(image_bytes).decode('ascii')
                mime = "image/jpeg"
                uri = "data:%s;base64,%s" % (mime, encoded)
                c['uri'] = uri
                c['desc'] = desc
                c['product_item_id'] = product_item_id   
                combined.append(c)

            # Close database connection
            cur.close()
            dbconn.close()

            # Set context variables for HTML template
            context = {
                'keyword': keyword,
                'combined': combined,
                'product_count': product_count,
            }
    
    # Render HTML template
    return render(request, 'store/vector.html', context)

####################### END SECTION - IMPLEMENT GENAI FEATURES FOR WORKSHOP ##########################