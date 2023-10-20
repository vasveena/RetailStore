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
#from skimage import io
import matplotlib.pyplot as plt
import requests
import psycopg2
from pgvector.psycopg2 import register_vector

# Initialize Bedrock client 
boto3_bedrock = bedrock.get_bedrock_client(assumed_role=os.environ.get("BEDROCK_ASSUME_ROLE", None), region=os.environ.get("AWS_DEFAULT_REGION", None))

# Initialize S3 client
s3 = boto3.client('s3')

# Initialize secrets manager
secrets = boto3.client('secretsmanager')

# Create your views here.

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
    if request.method == 'POST':
        try:
            reviews = ReviewRating.objects.get(user__id=request.user.id, product__id=product_id)
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
                data.user_id = request.user.id
                data.save()
                messages.success(request, 'Thank you! Your review has been submitted.')
                return redirect(url)


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


def generate_description(request, product_id):
   try:
        single_product = Product.objects.get(id=product_id)
        product_gallery = ProductGallery.objects.filter(product_id=single_product.id)

   except Exception as e:
        raise e
   
   context = {
        'single_product': single_product,
        'product_gallery': product_gallery,
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
    request.session['product_description_flag'] = True
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
            request.session['product_description_flag'] = False
            request.session.modified = True
            return redirect('generate_description', single_product.id)
        else:
            pass
    except:
        pass

def create_response(request, product_id, review_id):
    try:
        single_product = Product.objects.get(id=product_id)
        review = ReviewRating.objects.get(product=single_product, id=review_id)

    except Exception as e:
            raise e
    
    context = {
            'single_product': single_product,
            'review': review,
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

def generate_summary(request, product_id): 
    try:
        single_product = Product.objects.get(id=product_id)
        product_reviews = ReviewRating.objects.filter(product=single_product, status=True)

    except Exception as e:
            raise e
    
    context = {
            'single_product': single_product,
            'reviews': product_reviews,
        }
    return render(request, 'store/generate_summary.html', context)

def generate_review_summary(request, product_id):
    url = request.META.get('HTTP_REFERER')
    single_product = Product.objects.get(id=product_id)
    product_reviews = ReviewRating.objects.filter(product=single_product)

    review_digest = ''

    for review in product_reviews:
        review_digest += "<review>" + '\n'
        review_digest += review.review + '\n'
        review_digest += "</review>" + '\n\n'

    try:
        if 'Claude' in request.GET.get('llm'):
            textgen_llm = Bedrock(
                model_id="anthropic.claude-instant-v1",
                client=boto3_bedrock,
            )
            #Inference parameters for Claude Anthropic
            inference_modifier = {}
            inference_modifier['max_tokens_to_sample'] = int(request.GET.get('claude_max_tokens_to_sample') or 200)
            inference_modifier['temperature'] = float(request.GET.get('claude_temperature') or 0.5)
            inference_modifier['top_k'] = int(request.GET.get('claude_top_k') or 250)
            inference_modifier['top_p'] = float(request.GET.get('claude_top_p') or 1)
            inference_modifier['stop_sequences'] = ["\n\nHuman"]
        
        elif 'Titan' in request.GET.get('llm'):
            textgen_llm = Bedrock(
                model_id="amazon.titan-tg1-large",
                client=boto3_bedrock)

            #Inference parameters for Titan
            inference_modifier = {}
            inference_modifier['maxTokenCount'] = int(request.GET.get('titan_max_tokens_to_sample') or 200)
            inference_modifier['temperature'] = float(request.GET.get('titan_temperature') or 0.5)
            inference_modifier['topP'] = int(request.GET.get('titan_top_p') or 250)
            
        else:
            pass

        #create prompt
        multi_var_prompt = PromptTemplate(
            input_variables=["product_name","reviews"],
            template="""
            Human: Provide a review summary including pros and cons based on the customer reviews for the product {product_name}. This summary will be updated in the product webpage. Customer reviews are enclosed in <customer_reviews> tag. 
            <customer_reviews>
            {reviews}
            <customer_reviews>
            """)
        
        # Pass in form values to the prompt template
        prompt = multi_var_prompt.format(product_name=single_product.product_name,
                                         reviews=review_digest)

        response = textgen_llm(prompt)

        request.session['generated_summary'] = response
        request.session['summary_prompt'] = prompt
        request.session['summary_flag'] = True
        request.session.modified = True

    except: 
        pass

    return redirect(url)

def save_summary(request, product_id):
    try:
        single_product = Product.objects.get(id=product_id)
        request.session['summary_flag'] = False

        if 'save_summary' in request.POST:
            single_product.review_summary = request.session['generated_summary']
            single_product.save()
            success_message = "The summary for the review of " + single_product.product_name + " has been updated successfully. "
            messages.success(request, success_message)
            return redirect('product_detail', single_product.category.slug, single_product.slug)
        elif 'regenerate' in request.POST:
            request.session.modified = True
            return redirect('generate_summary', single_product.id)
        else:
            pass
    except:
        pass

def design_studio(request, product_id):
    single_product = Product.objects.get(id=product_id)
    context = {
        'single_product': single_product,
    }
    return render(request, 'store/studio.html', context)

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

def create_design_ideas(request, product_id):
    url = request.META.get('HTTP_REFERER')
    single_product = Product.objects.get(id=product_id)
    bucket_name = config('AWS_STORAGE_BUCKET_NAME')
    
    try:
        if 'delete_previous' in request.GET:
            # Delete existing gallery 
            print("image path: " +request.session['image_file_path'])
            product_gallery_del = ProductGallery.objects.filter(product=single_product, image=request.session['image_file_path'])
            if product_gallery_del:

                s3.delete_object(Bucket=bucket_name, Key=request.session['image_file_path'])
                product_gallery_del.delete()
                del request.session['image_flag']
            return redirect('create_design_ideas', single_product.id)
        
        if 'delete_all' in request.GET:
            # Delete existing gallery 
            del request.session['image_flag']
            product_gallery_del = ProductGallery.objects.filter(product=single_product)
            if product_gallery_del:
                for x in product_gallery_del:
                    key = x.image.url.split(".com/",1)[1]
                    s3.delete_object(Bucket=bucket_name, Key=key)
                product_gallery_del.delete()
            return redirect('create_design_ideas', single_product.id)
        
        image = Image.open(single_product.images)
        resize = image.resize((512,512))

        # Get inference parameters from form
        change_prompt = request.GET.get('change_prompt')
        negprompts = request.GET.get('negative_prompt')
        negative_prompts = []
        for negprompt in negprompts.split('\n'):
            negative_prompts.append(negprompt.replace('\r',''))
        start_schedule = float(request.GET.get('start_schedule')) or 0.5
        steps = int(request.GET.get('steps')) or 30
        cfg_scale = int(request.GET.get('cfg_scale')) or 10
        image_strength = float(request.GET.get('image_strength')) or 0.5
        denoising_strength = float(request.GET.get('denoising_strength')) or 0.5
        seed = int(request.GET.get('seed')) or random.randint(1, 1000000)
        style_preset = request.GET.get('style_preset') or "photographic"
        init_image_b64 = image_to_base64(resize)
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
        
        response = boto3_bedrock.invoke_model(body=sd_request, modelId="stability.stable-diffusion-xl")
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
            'media/store/products/' + image_file_path,
            ExtraArgs={
                'ACL': 'public-read'
            }
        )

        # Save generated image to database
        product_gallery = ProductGallery()
        product_gallery.product = single_product
        product_gallery.image = 'store/products/' + image_file_path
        product_gallery.save()
        
        request.session['change_prompt'] = change_prompt
        request.session['negative_prompt'] = negprompts
        request.session['image_file_path'] = 'store/products/' + image_file_path
        request.session['image_flag'] = True
        request.session['image_url'] = product_gallery.image.url
        request.session.modified = True
        messages.success(request, "Design idea saved!")
            
    except Exception as e: 
        print(e)
        
    return redirect(url)


def ask_question(request):
    context={}
    is_query_generated = False
    describe_query_result = ''

    if 'question' in request.GET:

        question = request.GET.get('question')
        print("question: " +question)
        s3 = boto3.client('s3')
        resp = s3.get_object(Bucket=config('AWS_STORAGE_BUCKET_NAME'), Key="data/postgres-schema.sql")
        schema = resp['Body'].read().decode("utf-8")
        prompt_template = """
            Human: Create an SQL query for a retail website to answer the question keeping the following rules in mind: 
            1. Database is implemented in Postgres.
            2. Enclose the query in <query></query>. 
            3. Do not use newline character or "\n". 
            4. Use "like" and upper() for string comparison on both left hand side and right hand side of the expression. For example, if the query contains "jackets", use "where upper(product_name) like upper('%jacket%')". 
            5. If the question is generic, like "where is mount everest" or "who went to the moon first", then do not generate any query in <query></query> and do not answer the question in any form. Instead, mention that the answer is not found in context.
            6. If the question is not related to the schema, then do not generate any query in <query></query> and do not answer the question in any form. Instead, mention that the answer is not found in context.  

            <schema>
                {schema}
            </schema>

            Question: {question}

            Assistant:"""

        multi_var_prompt = PromptTemplate(
                template=prompt_template, input_variables=["question","schema"]
                )
            
        llm = Bedrock(model_id="anthropic.claude-instant-v1", client=boto3_bedrock)
        
        prompt = multi_var_prompt.format(question=question, schema=schema)

        try: 
            llm_response = llm(prompt)

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
                print("generated query: " +query)

                response = secrets.get_secret_value(
                SecretId='postgresdb-secret')

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
                
                resultset = ''
                query_result = cursor.fetchall()
                dbconn.close()

                if len(query_result) > 0:
                    for x in query_result:
                        resultset = resultset + ''.join(str(x)) + "\n"

                print("resultset: " +resultset)

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

                multi_var_prompt = PromptTemplate(
                    template=prompt_template, input_variables=["question","resultset"]
                )

                prompt = multi_var_prompt.format(question=question, resultset=resultset)

                describe_query_result = llm(prompt)
                print("describe_query_result " + describe_query_result)

                if len(describe_query_result) == 0:
                    describe_query_result = "Sorry, I could not answer that question."

        except:
            query = "Sorry, I could not answer that question."

        context = {
            "question": question,
            "query": query,
            "is_query_generated": is_query_generated,
            "describe_query_result": describe_query_result,
        }

    return render(request, 'store/question.html', context)

def vector_search(request):
    if 'keyword' in request.GET:
        keyword = request.GET['keyword']
        if keyword:
            modelId = "amazon.titan-embed-g1-text-02"
            bedrock_embeddings = BedrockEmbeddings(model_id=modelId, client=boto3_bedrock)
            search_embedding = list(bedrock_embeddings.embed_query(keyword))
            response = secrets.get_secret_value(
                SecretId='postgresdb-secret'
            )
            database_secrets = json.loads(response['SecretString'])

            dbhost = database_secrets['host']
            dbport = database_secrets['port']
            dbuser = database_secrets['username']
            dbpass = database_secrets['password']
            dbname = database_secrets['vectorDbIdentifier']

            dbconn = psycopg2.connect(host=dbhost, user=dbuser, password=dbpass, port=dbport, database=dbname, connect_timeout=10)
            dbconn.set_session(autocommit=True)
            cur = dbconn.cursor()

            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            register_vector(dbconn)
            cur.execute("""CREATE INDEX ON vector_products 
               USING ivfflat (descriptions_embeddings vector_l2_ops) WITH (lists = 100);""")
            cur.execute("VACUUM ANALYZE vector_products;")

            #print(search_embedding)

            cur.execute("""SELECT id, url, description, descriptions_embeddings 
                        FROM vector_products 
                        ORDER BY descriptions_embeddings <-> %s limit 5;""", 
                        (np.array(search_embedding),))

            r = cur.fetchall()
            product_count = len(r)
            print("product_count: "+ str(product_count))

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

            cur.close()
            dbconn.close()
            context = {
                'keyword': keyword,
                'combined': combined,
                'product_count': product_count,
            }
    return render(request, 'store/vector.html', context)