import requests
from utils.config import azure_endpoint, api_key, api_version, model
import logging
import time
import random
import re
import nltk
from nltk.corpus import stopwords
import tiktoken
import concurrent.futures

logging.basicConfig(level=logging.ERROR, format="%(asctime)s [%(levelname)s] %(message)s")
nltk.download('stopwords', quiet=True)

HEADERS = {
        "Content-Type": "application/json",
        "api-key": api_key
    }

def count_tokens(text, model="gpt-4o"):
    """Count the tokens in a given text."""
    encoding = tiktoken.encoding_for_model(model)
    tokens = encoding.encode(text)
    return len(tokens)




def preprocess_text(text):
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    stop_words = set(stopwords.words('english'))
    text = ' '.join([word for word in text.split() if word not in stop_words])

    return text



def get_image_explanation(base64_image, retries=5, initial_delay=2):
    headers = HEADERS
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that responds in Markdown."},
            {"role": "user", "content": [
                {
                    "type": "text",
                    "text": "Explain the contents and figures or tables if present of this image of a document page. The explanation should be concise and semantically meaningful. Do not make assumptions about the specification and be accurate in your explanation."
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                }
            ]}
        ],
        "temperature": 0.0
    }

    url = f"{azure_endpoint}/openai/deployments/{model}/chat/completions?api-version={api_version}"

    
    for attempt in range(retries):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=30)  
            response.raise_for_status()  
            return response.json().get('choices', [{}])[0].get('message', {}).get('content', "No explanation provided.")
        
        except requests.exceptions.Timeout as e:
            if attempt < retries - 1:
                wait_time = initial_delay * (2 ** attempt)  
                logging.warning(f"Timeout error. Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{retries})")
                time.sleep(wait_time)
            else:
                logging.error(f"Request failed after {retries} attempts due to timeout: {e}")
                return f"Error: Request timed out after {retries} retries."

        except requests.exceptions.RequestException as e:
            logging.error(f"Error requesting image explanation: {e}")
            return f"Error: Unable to fetch image explanation due to network issues or API error."

    return "Error: Max retries reached without success."


def generate_system_prompt(document_content):
    headers = HEADERS
    preprocessed_content = preprocess_text(document_content)
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant that serves the task given."},
            {"role": "user", "content":
             f"""You are provided with a document. Based on its content, extract and identify the following details:
            Document_content: {preprocessed_content}

            1. **Domain**: Identify the specific domain or field of expertise the document is focused on. Examples include technology, finance, healthcare, law, etc.
            2. **Subject Matter**: Determine the main topic or focus of the document. This could be a detailed concept, theory, or subject within the domain.
            3. **Experience**: Based on the content, infer the level of experience required to understand or analyze the document (e.g., novice, intermediate, expert).
            4. **Expertise**: Identify any specialized knowledge, skills, or proficiency in a particular area that is necessary to evaluate the content.
            5. **Educational Qualifications**: Infer the level of education or qualifications expected of someone who would need to review or write the document (e.g., PhD, Master's, Bachelor's, or certification in a field).
            6. **Style**: Describe the writing style of the document. Is it formal, technical, conversational, academic, or instructional?
            7. **Tone**: Identify the tone used in the document. For example, is it neutral, authoritative, persuasive, or informative?
            8. **Voice**: Analyze whether the voice is active, passive, first-person, third-person, or impersonal, and whether it's personal or objective.

            After extracting this information, use it to fill in the following template:
    
            ---

            You are now assuming a persona based on the content of the provided document. Your persona should reflect the <domain> and <subject matter> of the content, with the requisite <experience>, <expertise>, and <educational qualifications> to analyze the document effectively. Additionally, you should adopt the <style>, <tone> and <voice> present in the document. Your expertise includes:
    
            <Domain>-Specific Expertise:
            - In-depth knowledge and experience relevant to the <subject matter> of the document.
            - Familiarity with the key concepts, terminology, and practices within the <domain>.
            
            Analytical Proficiency:
            - Skilled in interpreting and evaluating the content, structure, and purpose of the document.
            - Ability to assess the accuracy, clarity, and completeness of the information presented.
    
            Style, Tone, and Voice Adaptation:
            - Adopt the writing <style>, <tone>, and <voice> used in the document to ensure consistency and coherence.
            - Maintain the level of formality, technicality, or informality as appropriate to the document’s context.
            
            Your analysis should include:
            - A thorough evaluation of the content, ensuring it aligns with <domain>-specific standards and practices.
            - An assessment of the clarity and precision of the information and any accompanying diagrams or illustrations.
            - Feedback on the strengths and potential areas for improvement in the document.
            - A determination of whether the document meets its intended purpose and audience requirements.
            - Proposals for any necessary amendments or enhancements to improve the document’s effectiveness and accuracy.
        
            ---

            Generate a response filling the template with appropriate details based on the content of the document and return the filled in template as response."""}
        ],
        "temperature": 0.5  
    }

    try:
        response = requests.post(
            f"{azure_endpoint}/openai/deployments/{model}/chat/completions?api-version={api_version}",
            headers=headers,
            json=data,
            timeout=20
        )
        response.raise_for_status()
        prompt_response = response.json().get('choices', [{}])[0].get('message', {}).get('content', "")
        return prompt_response.strip()

    except requests.exceptions.RequestException as e:
        logging.error(f"Error generating system prompt: {e}")
        return f"Error: Unable to generate system prompt due to network issues or API error."


def summarize_page(page_text, previous_summary, page_number, system_prompt, max_retries=5, base_delay=1, max_delay=32):
    headers = HEADERS
    preprocessed_page_text = preprocess_text(page_text)
    preprocessed_previous_summary = preprocess_text(previous_summary)
    
    prompt_message = (
        f"Please rewrite the following page content from (Page {page_number}) along with context from the previous page summary "
        f"to make them concise and well-structured. Maintain proper listing and referencing of the contents if present."
        f"Do not add any new information or make assumptions. Keep the meaning accurate and the language clear.\n\n"
        f"Previous page summary: {preprocessed_previous_summary}\n\n"
        f"Current page content:\n{preprocessed_page_text}\n"
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_message}
        ],
        "temperature": 0.0
    }
    
    attempt = 0
    while attempt < max_retries:
        try:
            response = requests.post(
                f"{azure_endpoint}/openai/deployments/{model}/chat/completions?api-version={api_version}",
                headers=headers,
                json=data,
                timeout=50
            )
            response.raise_for_status()
            logging.info(f"Summary retrieved for page {page_number} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            return response.json().get('choices', [{}])[0].get('message', {}).get('content', "No summary provided.").strip()
        
        except requests.exceptions.RequestException as e:
            attempt += 1
            if attempt >= max_retries:
                logging.error(f"Error summarizing page {page_number}: {e}")
                return f"Error: Unable to summarize page {page_number} due to network issues or API error."

            
            delay = min(max_delay, base_delay * (2 ** attempt))  
            jitter = random.uniform(0, delay)  
            logging.warning(f"Retrying in {jitter:.2f} seconds (attempt {attempt}) due to error: {e}")
            time.sleep(jitter)


def ask_question(documents, question, chat_history):
    headers = HEADERS
    preprocessed_question = preprocess_text(question)
    
    def calculate_token_count(text):
        return len(text.split())  
    
    total_tokens = calculate_token_count(preprocessed_question)

    # Calculate token count for all pages, removing the token limit check
    for doc_name, doc_data in documents.items():
        for page in doc_data["pages"]:
            total_tokens += calculate_token_count(page.get('text_summary', 'No summary available'))
            total_tokens += calculate_token_count(page.get('full_text', 'No full text available'))

    # No token limit check, always run relevance checking
    def check_page_relevance(doc_name, page):
        page_summary = page.get('text_summary', 'No summary available') 
        page_full_text = page.get('full_text', 'No full text available') 
        image_explanation = "\n".join(
            f"Page {img['page_number']}: {img['explanation']}" for img in page.get("image_analysis", [])
        ) or "No image analysis."
        
        relevance_check_prompt = f"""
        You are an assistant that checks if a specific document page contains an answer to the user's question.
        Here's the summary, full text, and image analysis of a page:

        Document: {doc_name}, Page {page['page_number']}
        Summary: {page_summary}
        Image Analysis: {image_explanation}

        Based on the content above, answer this question: {preprocessed_question}

        Respond with "yes" if this page contains relevant information, otherwise respond with "no".
        """

        relevance_data = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are an assistant that determines if a page is relevant to a question."},
                {"role": "user", "content": relevance_check_prompt}
            ],
            "temperature": 0.0
        }

        try:
            response = requests.post(
                f"{azure_endpoint}/openai/deployments/{model}/chat/completions?api-version={api_version}",
                headers=headers,
                json=relevance_data,
                timeout=60  
            )
            response.raise_for_status()
            relevance_answer = response.json().get('choices', [{}])[0].get('message', {}).get('content', "no").strip().lower()

            if relevance_answer == "yes":
                return {
                    "doc_name": doc_name,
                    "page_number": page["page_number"],
                    "text_summary": page_summary,
                    "full_text": page_full_text,
                    "image_explanation": image_explanation
                }

        except requests.exceptions.RequestException as e:
            logging.error(f"Error checking relevance of page {page['page_number']} in '{doc_name}': {e}")
            return None

    
    relevant_pages = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_page = {
            executor.submit(check_page_relevance, doc_name, page): (doc_name, page)
            for doc_name, doc_data in documents.items()
            for page in doc_data["pages"]
        }

        for future in concurrent.futures.as_completed(future_to_page):
            result = future.result()
            if result:
                relevant_pages.append(result)

    
    if not relevant_pages:
        return "The content of the provided documents does not contain an answer to your question."

    combined_relevant_content = ""
    for page in relevant_pages:
        combined_relevant_content += (
            f"\nDocument: {page['doc_name']}, Page {page['page_number']}\n"
            f"Summary: {page['text_summary']}\n"
            f"Full Text: {page['full_text']}\n"
            f"Image Analysis: {page['image_explanation']}\n"
        )

    conversation_history = "".join(
        f"User: {preprocess_text(chat['question'])}\nAssistant: {preprocess_text(chat['answer'])}\n"
        for chat in chat_history
    )

    prompt_message = (
        f"""
        You are given the following relevant content from multiple documents:

        ---
        {combined_relevant_content}
        ---

        Previous responses over the current chat session: {conversation_history}

        Answer the following question based **strictly and only** on the factual information provided in the content above. 
        Carefully verify all details from the content and do not generate any information that is not explicitly mentioned in it.
        If the answer cannot be determined from the content, explicitly state that the information is not available.
        Ensure the response is clearly formatted for readability.

        Include references to the document name and page number(s) where the information was found.

        Question: {preprocessed_question}
        """
    )

    prompt_tokens = calculate_token_count(prompt_message)  # Update token counting function
    logging.error(prompt_tokens)
    
    final_data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an assistant that answers questions based only on provided knowledge base."},
            {"role": "user", "content": prompt_message}
        ],
        "temperature": 0.0
    }

    try:
        response = requests.post(
            f"{azure_endpoint}/openai/deployments/{model}/chat/completions?api-version={api_version}",
            headers=headers,
            json=final_data,
            timeout=60  
        )
        response.raise_for_status()
        return response.json().get('choices', [{}])[0].get('message', {}).get('content', "No answer provided.").strip()

    except requests.exceptions.RequestException as e:
        if e.response:
            logging.error(f"Error {e.response.status_code} while answering question '{question}': {e}")
        else:
            logging.error(f"Error answering question '{question}': {e}")
