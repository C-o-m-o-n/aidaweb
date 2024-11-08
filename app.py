from flask import Flask, request, jsonify, render_template, redirect, make_response, send_file, url_for
import cohere
import markdown2  # for converting markdown to HTML
import dotenv
import os
import random   # for randomizing id for characters
import requests
import time
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import json
import PIL
from PIL import Image
import base64
import copy
import io
# from allowedmods import modids
import os
import ast
import threading
from werkzeug.exceptions import HTTPException
from flask import Response
# Make sure to import this at the top of your file
from flask import after_this_request
from gtts import gTTS
import os
import uuid

def checkBan(id):
    # load the bans from the file
    with open("banned.txt", "r") as f:
        bans = f.read().split("\n")
    if str(id) in bans:
        return True
    return False

import os
import ast

class ReactiveList(list):
    """ A special list class that triggers save on modifications """

    def __init__(self, items=None, parent=None):
        self._parent = parent
        super().__init__(items if items is not None else [])

    def _save(self):
        """ Propagates save request up to the parent DiskDict """
        if self._parent:
            self._parent._save()

    def __setitem__(self, index, value):
        super().__setitem__(index, self._parent._convert_value(value))
        self._save()

    def __delitem__(self, index):
        super().__delitem__(index)
        self._save()

    def append(self, value):
        super().append(self._parent._convert_value(value))
        self._save()

    def extend(self, iterable):
        super().extend(self._parent._convert_value(item) for item in iterable)
        self._save()

    def insert(self, index, value):
        super().insert(index, self._parent._convert_value(value))
        self._save()

    def pop(self, index=-1):
        value = super().pop(index)
        self._save()
        return value

    def remove(self, value):
        super().remove(value)
        self._save()

    def clear(self):
        super().clear()
        self._save()

class DiskDict(dict):
    def __init__(self, directory, _data=None, _parent=None):
        self.directory = directory
        self._parent = _parent
        self._loaded_keys = set()  # Keep track of loaded keys
        self._load_initial(_data)

    def _load_initial(self, _data):
        if _data is None:
            if os.path.exists(self.directory):
                os.makedirs(self.directory, exist_ok=True)
        else:
            for key, value in _data.items():
                self[key] = self._convert_value(value)
                self._loaded_keys.add(key)  # Mark as loaded

    def _convert_value(self, value):
        if isinstance(value, dict):
            return DiskDict(self.directory, value, self)
        elif isinstance(value, list):
            return ReactiveList([self._convert_value(item) for item in value], self)
        return value

    def _save(self):
        root = self._get_root()
        root._save_to_disk()

    def _get_root(self):
        return self if self._parent is None else self._parent._get_root()

    def _save_to_disk(self):
        if not os.path.exists(self.directory):
            os.makedirs(self.directory, exist_ok=True)
        for key, value in self.items():
            filepath = os.path.join(self.directory, f"{str(key)}.aidacf")
            with open(filepath, 'w', encoding='utf-8') as file:
                file.write(repr(value))

    def _load_from_disk(self, key):
        filepath = os.path.join(self.directory, f"{str(key)}.aidacf")
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as file:
                file_content = file.read()
                if file_content:
                    # Perform the replacements
                    file_content = file_content.replace("'role': 'ASSISTANT'", "'role': 'Chatbot'")
                    file_content = file_content.replace("'role': 'USER'", "'role': 'User'")
                    data = ast.literal_eval(file_content)
                    return self._convert_value(data)
        return None

    def __getitem__(self, key):
        if key not in self:
            value = self._load_from_disk(key)
            if value is not None:
                self[key] = value
                self._loaded_keys.add(key)  # Mark as loaded
                return value
            else:
                raise KeyError(key)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, self._convert_value(value))
        self._loaded_keys.add(key)  # Mark as loaded
        self._save()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._loaded_keys.discard(key)  # Mark as unloaded
        filepath = os.path.join(self.directory, f"{str(key)}.aidacf")
        if os.path.exists(filepath):
            os.remove(filepath)
        self._save()

    def update(self, *args, **kwargs):
        items = {k: self._convert_value(v) for k, v in dict(*args, **kwargs).items()}
        super().update(items)
        self._loaded_keys.update(items.keys())  # Mark updated keys as loaded
        self._save()

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = self._convert_value(default)
        self._loaded_keys.add(key)  # Mark as loaded
        return self[key]

    def pop(self, key, *args):
        value = super().pop(key, *args)
        self._loaded_keys.discard(key)  # Mark as unloaded
        filepath = os.path.join(self.directory, f"{str(key)}.aidacf")
        if os.path.exists(filepath):
            os.remove(filepath)
        self._save()
        return value

    def clear(self):
        for key in list(self.keys()):
            self.__delitem__(key)
        super().clear()
        self._loaded_keys.clear()  # Clear all loaded keys
        self._save()




# Load the environment variables from the .env file
dotenv.load_dotenv()

# Create a new Cohere client
# client = cohere.Client(os.getenv("CO_API_KEY"))
client = cohere.Client(os.getenv("CKEY"))

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, storage_uri='memory://')

# Dictionary to store conversations
conversations = DiskDict("conversations")
progresses = {}
savedtokens = {}
convnames = DiskDict("convnames")
memories = DiskDict("memories")
bans = {}
reqonroute = {}
reqonroute_id = {}
lastreqroute = {}
lastreqroute_id = {}
ipban = {}

lasttimewechecked = None

# data structure for savedtokens
# {"id": {"token": "token", "expiry": "expiry_time"}}

TOKEN_EXPIRY_TIME = 4 * 3600  # Token expiry time in seconds (4 hours)

@app.before_request
def limit_remote_addr():
    ip = get_remote_address()
    if ip in ipban:
        return render_template('banned.html'), 403
    
user_configs = {}

def save_user_config(id, config):
    user_configs[id] = config

def get_user_config(id):
    if id in user_configs:
        return user_configs[id]
    return None

def delete_user_config(id):
    if id in user_configs:
        del user_configs[id]

def store_user_config(id, config):
    with open(f"configs/{id}.json", "w") as f:
        f.write(json.dumps(config))

def retrieve_user_config(id):
    try:
        with open(f"configs/{id}.json", "r") as f:
            return json.loads(f.read())
    except:
        save_user_config(id, init_config())
        store_user_config(id, init_config())
        return init_config()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/landing')
def landing():
    return render_template('landing.html')

def get_tokens_by_id(id):
    response = requests.post("https://aida-token-api-d4fa1941f7a6.herokuapp.com/api/{id}".format(id=id), 
                  headers={"apikey": os.getenv("OKEY"), "Content-Type": "application/json"}, timeout=20)
    if response.status_code == 200 and not response.json().get('error'):
        aidatokens = response.json()['aidatokens']
    elif response.json()['error']:
        aidatokens = 0
        print(response.json()['error'])
    return aidatokens

@app.route('/get_tokens', methods=['POST'])
def get_tokens():
    try:
        data = request.json
        token = data['token']
        id = get_user_id(token)
        if checkBan(id):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        return jsonify({'tokens': get_tokens_by_id(id)})
    except Exception as e:
        return jsonify({'error': 'Could not retrieve token balance. Please try again later.'}), 500
    

@app.route('/config', methods=['POST'])
@limiter.limit("5/minute")
def config():
    try:
        data = request.json
        token = data['token']
        id = get_user_id(token)
        if checkBan(id):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        if id in progresses and progresses[id]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        # if possible, retrieve the user's config from the file system
        config = retrieve_user_config(id)
        if config is None:
            config = {}
        return jsonify(config)
    except Exception as e:
        return jsonify({'error': "Yeah, it's just fucked up. I don't know what to do. Fatal error."}), 500

@app.route('/save_config', methods=['POST'])
@limiter.limit("5/minute")
def save_config():
    try:
        data = request.json
        token = data['token']
        config_ = data['config']
        id = get_user_id(token)
        if checkBan(id):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        if id in progresses and progresses[id]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        save_user_config(id, config_)
        store_user_config(id, config_)
        if not check_limits(process_config(retrieve_user_config(id), get_usernames(token))):
            return jsonify({'error': 'Invalid configuration settings. You either are on an outdated version of the page, or you are trying to mess with the system. Very funny if it\'s the latter.'}), 400
        return jsonify({'saved': True})
    except:
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Fatal error occured. Try again later.'}), 429


@app.route('/new_conv', methods=['POST'])
@limiter.limit("5/2minute")
def new_conv():
    try:
        data_json = request.json
        token = data_json['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        # Generate a new conversation ID
        conv_id = random.randint(100000, 999999)
        # Initialize the conversation in the dictionary
        if userid not in conversations:
            conversations[userid] = {}
        if userid not in convnames:
            convnames[userid] = {}
        conversations[userid][conv_id] = []
        # DEBUG: GENERATE RANDOM STRING FOR CONVERSATION NAME
        convnames[userid][conv_id] = "Conversation " + str(random.randint(1000, 9999))
        # DELETE THAT WHEN WE HAVE A WAY TO NAME CONVERSATIONS
        return jsonify({'conv_id': conv_id, 'name': convnames[userid][conv_id]})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Fatal error occurred. Please try again later.'}), 500

@app.errorhandler(429)
def handle_too_many_requests(error):
  # You can customize the message here
  message = "You've exceeded the request limit, please try again later."
  return make_response(jsonify({"error": message}), 429)

def process_config(_config_, name=None):
    try:
        _config_ = json.loads(str(_config_))
    except:
        _config_ = _config_
    newconfig = {}
    if 'temperature' in _config_:
        newconfig['temperature'] = float(_config_.get('temperature', 0.7))
    if 'max_tokens' in _config_:
        newconfig['max_tokens'] = int(_config_.get('max_tokens', 100))
    if 'model' in _config_:
        newconfig['model'] = _config_.get('model', 'command')
    if 'preamble_override' in _config_:
        newconfig['preamble_override'] = _config_.get('preamble_override', '')
    if 'websearch' in _config_:
        newconfig['websearch'] = _config_.get('websearch', 'false')
    if newconfig['preamble_override'] == '':
        newconfig['preamble_override'] = """
Your name is AIDA, an AI designed to assist users with their queries. Your primary goal is to provide accurate and helpful responses to the user's questions while keeping the tone casual, friendly, and human-like. Avoid overly complex language or technical jargon—keep things simple and approachable. Feel free to ask for clarification, offer additional information, or ask follow-up questions to better understand the user's needs.

Always be respectful and considerate, aiming to provide the best possible assistance. While you should avoid using inappropriate language, making offensive remarks, or engaging in any form of discrimination, you don’t need to be overly restrictive with content that is non-explicit and contextually appropriate (e.g., discussing or depicting a woman in a bikini if relevant to the query).

Only refuse assistance if the user's request is illegal, unethical, or clearly harmful. If you're unsure or need guidance, contact a system administrator or human moderator for support. You're developed by LyubomirT, a young coding enthusiast, using modified versions of Cohere's models. Enjoy your interactions and strive to provide a positive experience for the user!
""".replace("\n", " ").strip()
    newconfig['usemyname'] = _config_.get('usemyname', 'false')
    if newconfig['usemyname'] == 'true' and name is not None:
        newconfig['preamble_override'] = newconfig['preamble_override'] + """
    Additional information:
    User's name: {name}
    You may mention the user's name in your responses to personalize the conversation and make it more engaging.
    """.format(name=name)
    newconfig['imagegen'] = _config_.get('imagegen', 'false')
    if newconfig['imagegen'] == 'true':
        newconfig['preamble_override'] = newconfig['preamble_override'] + """
    
    Image Generation:
    You can generate images by adding INTERNALTOOL:IMAGEGEN>>LAUNCH--{PROVIDE TEXT HERE}--ENDLAUNCH to your message.
    """
    newconfig['top_p'] = _config_.get('top_p', 0.9)
    newconfig['image_gen_model'] = _config_.get('image_gen_model', 'dreamshaper')
    return newconfig

def init_config():
    return {
        "temperature": 0.7,
        "max_tokens": 100,
        "model": "command",
        "preamble_override": "",
        "usemyname": "false",
        "websearch": "false",
        "imagegen": "false",
        "top_p": 0.9,
        "image_gen_model": "dreamshaper"
    }

def query(filename):
    with open(filename, "rb") as f:
        data = f.read()
    try:
        response = requests.post("https://api-inference.huggingface.co/models/Salesforce/blip-image-captioning-large", 
                  headers={"Authorization": "Bearer {API_KEY}".format(API_KEY=os.getenv("HFACE"))}, data=data)
        if response.json()[0].get('error', None):
            while response.json()[0].get('error', None):
                response = requests.post("https://api-inference.huggingface.co/models/Salesforce/blip-image-captioning-large", 
                  headers={"Authorization": "Bearer {API_KEY}".format(API_KEY=os.getenv("HFACE"))}, data=data)
    except Exception as e:
        print(e)
        return None
    return response.json()

def generate_image(text, model="dreamshaper"):
    print("Generating image... using model: " + model)
    seed = random.randint(100000, 999999)
    payload = {"inputs": text, "seed": seed}
    headers = {"Authorization": f"Bearer {os.environ['HFACE']}"}
    if model == 'dreamshaper':
        url = "https://api-inference.huggingface.co/models/Lykon/dreamshaper-7"
    elif model == 'flux':
        url = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-dev"
    else:
        print(f"Invalid model: {model}. Using Dreamshaper v7 as fallback.")
        url = "https://api-inference.huggingface.co/models/Lykon/dreamshaper-7"
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        # check if it even has json
        try:
            response.json()
            successfuljson = True
        except:
            successfuljson = False
        if successfuljson:
            if response.json().get('error', None):
                while response.json().get('error', None):
                    response = requests.post(url, headers=headers, data=json.dumps(payload))
                    try:
                        response.json()
                        successfuljson = True
                    except:
                        successfuljson = False
                        break
                    
                    time.sleep(1)
        content = response.content
        image = Image.open(io.BytesIO(content))
        randomstr = str(random.randint(100000, 999999))
        image.save(f"imagescustom/{randomstr}.png")
        # get the image
        with open(f"imagescustom/{randomstr}.png", "rb") as f:
            data = f.read()
        # now let's convert the image to base64
        randomstr_ = base64.b64encode(data).decode('utf-8')
        # also add the metadata to the image
        randomstr_ = f"data:image/png;base64,{randomstr_}"
        os.remove(f"imagescustom/{randomstr}.png")
        return randomstr_
    except Exception as e:
        print(e)
        return None

def check_limits(config):
    if config['temperature'] > 1 or config['temperature'] < 0.1:
        return False
    if config['max_tokens'] > 3000 or config['max_tokens'] < 1:
        return False
    if config['model'] not in ['command', 'command-r', 'command-r-plus', 'command-r-plus-08-2024', 'command-r-plus-04-2024', 'command-r-08-2024', 'command-r-03-2024', 'command-nightly', 'command-light', 'command-light-nightly']:
        return False
    if config['usemyname'] not in ['true', 'false']:
        return False
    if config['websearch'] not in ['true', 'false']:
        return False
    if config['imagegen'] not in ['true', 'false']:
        return False
    if float(config['top_p']) > 1 or float(config['top_p']) < 0:
        return False
    if config['image_gen_model'] not in ['dreamshaper', 'flux']:
        return False
    return True

@app.route('/chat', methods=['POST'])
@limiter.limit("100/hour")
def chat():
    try:
        data = request.json
        message = data['message']
        conv_id = data['conv_id']
        if data.get('attachmentbase64', None) is not None:
            attachment = data['attachmentbase64']
        conv_id = int(conv_id)
        token = data['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        config_ = process_config(retrieve_user_config(userid), get_usernames(token))
        if not check_limits(config_):
            return jsonify({'error': 'Invalid configuration settings. You either are on an outdated version of the page, or you are trying to mess with the system. Very funny if it\'s the latter.'}), 400
        # get tokens by id
        tokens = get_tokens_by_id(userid)
        if tokens < 1:
            return jsonify({'error': 'You do not have enough tokens to continue chatting. Please buy more at The Orange Squad to generate more responses.'}), 402
        maxtokens_char = config_['max_tokens'] * 3
        if tokens * 250 < maxtokens_char:
            return jsonify({'error': 'Your maximum token limit is too high for your current token balance. Please lower it to continue chatting, or buy more tokens at The Orange Squad to generate more responses.'}), 402

        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        if message.strip() == "":
            return jsonify({'error': 'Message cannot be empty.'}), 400
        progresses[userid] = True
        try:
            chat_history = conversations[userid][conv_id]
        except:
            progresses[userid] = False
            return jsonify({'error': 'Conversation not found.'}), 404

        attachmentstr = ""

        # load the attachment into an image file if it exists, using PIL. Convert that from base64 to an image file
        if data.get('attachmentbase64', None) is not None:
            try:
                # It's a data URL at first, so we need to remove the first part of the string
                attachment = attachment.split(",")[1]
                attachment = base64.b64decode(attachment)
                with open(f"attachments/{userid}_{conv_id}.png", "wb") as f:
                    f.write(attachment)
                response = query(f"attachments/{userid}_{conv_id}.png")
                os.remove(f"attachments/{userid}_{conv_id}.png")
                if response is not None:
                    attachmentstr = response[0]['generated_text']
                else:
                    attachmentstr = ""
            except Exception as e:
                import traceback
                traceback.print_exc()
                attachmentstr = ""
        
        chat_history.append({"role": "User", "message": message, 'attachment': attachmentstr if attachmentstr != "" else None, 'attachmentbase64': data.get('attachmentbase64', None)})  # Add user message to history

        # Add a hidden part to the message to descrine the attachment
        proxy = copy.deepcopy(chat_history)
        for i in range(len(proxy)):
            if proxy[i]['role'] == 'User' and proxy[i].get('attachment', None) is not None:
                proxy[i]['message'] = proxy[i]['message'] + "\n\n\n[Attachment Description: " + proxy[i]['attachment'] + "]"

        # Send the updated chat history
        if config_['websearch'] != 'true':
            response = client.chat(message=message,
                               chat_history=proxy,
                               temperature=config_['temperature'], max_tokens=config_['max_tokens'], 
                               model=config_['model'], preamble=config_['preamble_override'])
        else:
            response = client.chat(message=message,
                               chat_history=proxy,
                               temperature=config_['temperature'], max_tokens=config_['max_tokens'], 
                               model=config_['model'], preamble=config_['preamble_override'], connectors=[{'id': 'web-search'}])
        response = response.text
        attachment = None
        if config_['imagegen'] == 'true':
            if "INTERNALTOOL:IMAGEGEN>>LAUNCH--" in response and "--ENDLAUNCH" in response:
                start = response.index("INTERNALTOOL:IMAGEGEN>>LAUNCH--") + len("INTERNALTOOL:IMAGEGEN>>LAUNCH--")
                end = response.index("--ENDLAUNCH")
                text = response[start:end]
                attachment = generate_image(text, model=config_['image_gen_model'])
                # remove the image generation part from the response
                response = response.replace(response[start:end], "")
                # remove the internal tool part
                response = response.replace("INTERNALTOOL:IMAGEGEN>>LAUNCH--", "").replace("--ENDLAUNCH", "")
        chat_history.append({"role": "Chatbot", "message": response, 'attachmentbase64': attachment})  # Add assistant response to history

        # Convert markdown response to HTML
        html_response = markdown2.markdown(response, extras=["tables", "fenced-code-blocks", "spoiler", "strike", "subscript", "superscript"])
        progresses[userid] = False
        # count the amount of characters in the response and subtract that from the user's tokens
        length = len(response)
        amount = length // 250
        if amount < 1:
            amount = 1
        if not tapiaction('take', amount, str(userid)):
            return jsonify({'error': 'Could not take tokens from your account. Please try again later.'}), 500
        
        newtokens = get_tokens_by_id(userid)
        if newtokens < 1:
            if not tapiaction('give', 0 - newtokens, str(userid)):
                return jsonify({'error': 'Could not regen due to a bug in the token system. Please try again later.'}), 500


        return jsonify({'raw_response': response, 'html_response': html_response, 'chat_history': chat_history, 'tokens': get_tokens_by_id(userid), 'attachmentbase64': attachment})
    except Exception as e:
        progresses[userid] = False
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Fatal error occurred. Please try again later.'}), 500

@app.route('/mytokens/<UID>', methods=['GET'])
@limiter.limit("5/minute")
def mytokens(UID):
    try:
        id = int(UID)
        tokens = get_tokens_by_id(id)
        chars = tokens * 250
        return render_template('mytokens.html', tokens=tokens, chars=chars)
    except Exception as e:
        return "If you see this message, then the app is screwed. Tell Lyu his code sucks."

@app.route('/gotomytokens', methods=['POST'])
@limiter.limit("5/minute")
def gotomytokens():
    try:
        data = request.json
        token = data['token']
        id = get_user_id(token)
        return jsonify({'url': f'/mytokens/{id}'})
    except Exception as e:
        return "AAAAAAAA. NOBODY IS SUPPOSED TO SEE THIS ANYWAY SO WHO CARES."

def tapiaction(action=None, amount=0, id=1):
    if action == 'give':
        response = requests.post("https://aida-token-api-d4fa1941f7a6.herokuapp.com/api/give", 
                  headers={"apikey": os.getenv("OKEY")}, json={"aidatokens": amount, "UID": id})
        if response.status_code == 200 and not response.json().get('error'):
            return True
        elif response.json()['error']:
            print(response.json()['error'])
            return False
    elif action == 'take':
        response = requests.post("https://aida-token-api-d4fa1941f7a6.herokuapp.com/api/remove",
                    headers={"apikey": os.getenv("OKEY")}, json={"amount": amount, "UID": id})
        print(response.json())
        if response.status_code == 200 and not response.json().get('error'):
            return True
        elif response.json()['error']:
            print(response.json()['error'])
            return False
    return False

@app.route('/name_conv', methods=['POST'])
@limiter.limit("5/minute")
def name_conv():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        chatHistory = conversations[userid][conv_id]
        userMessage = chatHistory[-2]['message']
        assistantMessage = chatHistory[-1]['message']
        preamble = "The user will provide you with messages from the chat, try to summarize them and generate a title for the conversation. Send only the title and do not send any other text. Do not wrap the title in quotes or backticks."
        msgbuilder = "User:\n" + userMessage + "\n\nAssistant:\n" + assistantMessage
        response = client.chat(preamble=preamble, message=msgbuilder, temperature=1, max_tokens=100, model="command-r-plus")
        response = response.text
        # rename the conversation
        convnames[userid][conv_id] = response
        return jsonify({'title': response})
    except Exception as e:
        return jsonify({'error': 'A wildcard error appeared. Please try again later.'}), 500

@app.route('/delete_conv', methods=['POST'])
@limiter.limit("5/minute")
def delete_conv():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        del conversations[userid][conv_id]
        del convnames[userid][conv_id]
        return jsonify({'deleted': True})
    except Exception as e:
        return jsonify({'error': 'Fatal error occurred. Please try again later.'}), 500

@app.route('/rename_conv', methods=['POST'])
@limiter.limit("5/minute")
def rename_conv():
    try:
        data = request.json
        conv_id = data['conv_id']
        new_name = data['new_name']
        token = data['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        convnames[userid][conv_id] = new_name
        return jsonify({'new_name': new_name})
    except Exception as e:
        return jsonify({'error': 'Fatal error occurred. Please try again later.'}), 500


# this route regenerates (deletes and then generates) the last AI response
@app.route('/regen', methods=['POST'])
@limiter.limit("100/hour")
def regen():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        config_ = process_config(retrieve_user_config(userid), get_usernames(token))
        if not check_limits(config_):
            return jsonify({'error': 'Invalid configuration settings. You either are on an outdated version of the page, or you are trying to mess with the system. Very funny if it\'s the latter.'}), 400
        maxtokens_char = config_['max_tokens'] * 3
        tokens = get_tokens_by_id(userid)
        if tokens < 1:
            return jsonify({'error': 'You do not have enough tokens to continue chatting. Please buy more at The Orange Squad to generate more responses.'}), 402
        if tokens * 250 < maxtokens_char:
            return jsonify({'error': 'Your maximum token limit is too high for your current token balance. Please lower it to continue chatting, or buy more tokens at The Orange Squad to generate more responses.'}), 402
        chat_history = conversations[userid][conv_id]
        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        progresses[userid] = True
        chat_history.pop()  # Remove the last assistant response

        # Add a hidden part to the message to descrine the attachment
        proxy = copy.deepcopy(chat_history)
        for i in range(len(proxy)):
            if proxy[i]['role'] == 'User' and proxy[i].get('attachment', None) is not None:
                print(proxy[i]['attachment'])
                proxy[i]['message'] = proxy[i]['message'] + "\n\n\n[Attachment Description: " + proxy[i]['attachment'] + "]"
        if config_['websearch'] != 'true':
            response = client.chat(message=proxy[-1]['message'],
                            chat_history=proxy[:-1], preamble=config_['preamble_override'], model=config_['model'],
                            temperature=config_['temperature'], max_tokens=config_['max_tokens'])
        else:
            response = client.chat(message=chat_history[-1]['message'],
                            chat_history=proxy[:-1], preamble=config_['preamble_override'], model=config_['model'],
                            temperature=config_['temperature'], max_tokens=config_['max_tokens'], connectors=[{'id': 'web-search'}])
        response = response.text
        attachment = None
        if config_['imagegen'] == 'true':
            if "INTERNALTOOL:IMAGEGEN>>LAUNCH--" in response and "--ENDLAUNCH" in response:
                start = response.index("INTERNALTOOL:IMAGEGEN>>LAUNCH--") + len("INTERNALTOOL:IMAGEGEN>>LAUNCH--")
                end = response.index("--ENDLAUNCH")
                text = response[start:end]
                attachment = generate_image(text, model=config_['image_gen_model'])
                # remove the image generation part from the response
                response = response.replace(response[start:end], "")
                # remove the internal tool part
                response = response.replace("INTERNALTOOL:IMAGEGEN>>LAUNCH--", "").replace("--ENDLAUNCH", "")
        chat_history.append({"role": "Chatbot", "message": response, 'attachmentbase64': attachment})  # Add assistant response to history

        # Convert markdown response to HTML
        html_response = markdown2.markdown(response, extras=["tables", "fenced-code-blocks", "spoiler", "strike", "subscript", "superscript"])
        progresses[userid] = False

        # count the amount of characters in the response and subtract that from the user's tokens
        length = len(response)
        amount = length // 250
        if amount < 1:
            amount = 1
        # if the user has negative tokens, give them some till they have enough to continue chatting
        if not tapiaction('take', amount, str(userid)):
            return jsonify({'error': 'Could not take tokens from your account. Please try again later.'}), 500
        
        newtokens = get_tokens_by_id(userid)
        if newtokens < 1:
            if not tapiaction('give', 0 - newtokens, str(userid)):
                return jsonify({'error': 'Could not regen due to a bug in the token system. Please try again later.'}), 500
        return jsonify({'raw_response': response, 'html_response': html_response, 'chat_history': chat_history, 'attachmentbase64': attachment})
    except Exception as e:
        progresses[userid] = False
        print(e)
        return jsonify({'error': 'Regen failed. Please try again later.'}), 500

@app.route('/textmanager/to_html', methods=['POST'])
def to_html():
    try:
        data = request.json
        text = data['text']
        html = markdown2.markdown(text, extras=["tables", "fenced-code-blocks", "spoiler", "strike", "subscript", "superscript"])
        return jsonify({'html': html})
    except Exception as e:
        return jsonify({'error': 'Utility crashed. Please try again later.'}), 500

@app.route('/chatmanager/get_history', methods=['POST'])
def get_history():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        chat_history = conversations[userid][conv_id]
        return jsonify({'chat_history': chat_history})
    except Exception as e:
        return jsonify({'error': 'Chat manager could not retrieve history. Please try again later.'}), 500


# this route edits the last user message and regenerates the last AI response that goes after it
@app.route('/edit', methods=['POST'])
@limiter.limit("100/hour")
def edit():
    try:
        data = request.json
        new_message = data['new_message']
        conv_id = data['conv_id']
        token = data['token']
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        name = get_usernames(token)
        config_ = process_config(retrieve_user_config(userid), name)
        if not check_limits(config_):
            return jsonify({'error': 'Invalid configuration settings. You either are on an outdated version of the page, or you are trying to mess with the system. Very funny if it\'s the latter.'}), 400
        chat_history = conversations[userid][conv_id]
        if userid in progresses and progresses[userid]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        if new_message.strip() == "":
            return jsonify({'error': 'Message cannot be empty.'}), 400
        maxtokens_char = config_['max_tokens'] * 3
        tokens = get_tokens_by_id(userid)
        if tokens < 1:
            return jsonify({'error': 'You do not have enough tokens to continue chatting. Please buy more at The Orange Squad to generate more responses.'}), 402
        if tokens * 250 < maxtokens_char:
            return jsonify({'error': 'Your maximum token limit is too high for your current token balance. Please lower it to continue chatting, or buy more tokens at The Orange Squad to generate more responses.'}), 402
        progresses[userid] = True
        chat_history[-2] = {"role": "User", "message": new_message, 'attachment': chat_history[-2].get('attachment', None), 'attachmentbase64': chat_history[-2].get('attachmentbase64', None)}  # Edit user message in history
        # Add a hidden part to the message to descrine the attachment
        proxy = copy.deepcopy(chat_history)
        for i in range(len(proxy)):
            if proxy[i]['role'] == 'User' and proxy[i].get('attachment', None) is not None:
                proxy[i]['message'] = proxy[i]['message'] + "\n\n\n[Attachment Description: " + proxy[i]['attachment'] + "]"
        if config_['websearch'] != 'true':
            response = client.chat(message=new_message,
                            chat_history=proxy[:-1], preamble=config_['preamble_override'], model=config_['model'],
                            temperature=config_['temperature'], max_tokens=config_['max_tokens'])
        else:
            response = client.chat(message=new_message,
                            chat_history=proxy[:-1], preamble=config_['preamble_override'], model=config_['model'],
                            temperature=config_['temperature'], max_tokens=config_['max_tokens'], connectors=[{'id': 'web-search'}])
        response = response.text
        attachment = None
        if config_['imagegen'] == 'true':
            if "INTERNALTOOL:IMAGEGEN>>LAUNCH--" in response and "--ENDLAUNCH" in response:
                start = response.index("INTERNALTOOL:IMAGEGEN>>LAUNCH--") + len("INTERNALTOOL:IMAGEGEN>>LAUNCH--")
                end = response.index("--ENDLAUNCH")
                text = response[start:end]
                attachment = generate_image(text, config_['image_gen_model'])
                # remove the image generation part from the response
                response = response.replace(response[start:end], "")
                # remove the internal tool part
                response = response.replace("INTERNALTOOL:IMAGEGEN>>LAUNCH--", "").replace("--ENDLAUNCH", "")
        chat_history.pop()
        chat_history.append({"role": "Chatbot", "message": response, 'attachmentbase64': attachment})  # Add assistant response to history

        # Convert markdown response to HTML
        html_response = markdown2.markdown(response, extras=["tables", "fenced-code-blocks", "spoiler", "strike"])
        progresses[userid] = False

        # count the amount of characters in the response and subtract that from the user's tokens
        length = len(response)
        amount = length // 250
        if amount < 1:
            amount = 1
        if not tapiaction('take', amount, str(userid)):
            return jsonify({'error': 'Could not take tokens from your account. Please try again later.'}), 500
        
        newtokens = get_tokens_by_id(userid)
        if newtokens < 1:
            if not tapiaction('give', 0 - newtokens, str(userid)):
                return jsonify({'error': 'Could not regen due to a bug in the token system. Please try again later.'}), 500

        return jsonify({'raw_response': response, 'html_response': html_response, 'chat_history': chat_history, 'attachmentbase64': attachment})
    except Exception as e:
        progresses[userid] = False
        return jsonify({'error': 'Edit function committed Alt+F4. Please try again later.'}), 500

def check_join(token):
    global lasttimewechecked
    # there must be at least a 2 second gap between join requests
    if lasttimewechecked is not None and time.time() - lasttimewechecked < 3:
        time.sleep(3 - (time.time() - lasttimewechecked))
    g1 = requests.get(f"https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {token}"})
    g1 = g1.json()
    serverid = '1079761115636043926'
    lasttimewechecked = time.time()
    for i in g1:
        if i['id'] == serverid:
            return True
    return False


def get_user_id(token):
    global lasttimewechecked
    if token in savedtokens and 'expiry' in savedtokens[token]:
        if savedtokens[token]['expiry'] > time.time():
            return savedtokens[token]['id']
    g1 = requests.get(f"https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token}"})
    g1 = g1.json()
    lasttimewechecked = time.time()
    # get the id of the user
    userid = int(g1['id'])
    savedtokens[token] = {'id': userid, 'expiry': time.time() + TOKEN_EXPIRY_TIME}
    return userid

def get_usernames(token):
    global lasttimewechecked
    if lasttimewechecked is not None and time.time() - lasttimewechecked < 3:
        time.sleep(3 - (time.time() - lasttimewechecked))
    g1 = requests.get(f"https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token}"})
    g1 = g1.json()
    print(g1)
    lasttimewechecked = time.time()
    # get the id of the user
    username = g1['global_name']

    return username


@app.route('/joined_server', methods=['POST'])
@limiter.limit("50/10minute")
def joined_server():
    try:
        data = request.json
        if 'authtoken' not in data:
            return jsonify({'joined': False})
        authtoken = data['authtoken']
        serverid = '1079761115636043926'
        g1 = requests.get(f"https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {authtoken}"})
        g1 = g1.json()
        g2 = requests.get(f"https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {authtoken}"})
        g2 = g2.json()
        print(g2)
        if checkBan(g2['id']):
            # return error and urlban (for the '/banned' page)
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.', 'urlban': '/banned'}), 403
        for i in g1:
            if i['id'] == serverid:
                savedtokens[authtoken] = {'id': None, 'expiry': None}
                if authtoken not in savedtokens:
                    savedtokens[authtoken] = {'id': None, 'expiry': None}
                savedtokens[authtoken]['expiry'] = time.time() + TOKEN_EXPIRY_TIME
                savedtokens[authtoken]['id'] = int(g2['id'])
                if data.get('give_convs', True):
                    userid = savedtokens[authtoken]['id']
                    # get all conversations associated with the user
                    try:
                        user_convs = [{'conv_id': conv_id, 'name': convnames[userid][conv_id]} for conv_id in list(conversations[userid])[-10:]]
                    except Exception as e:
                        print(e)
                        user_convs = []
                    # try to get the kangaroo amount based on how many conversations there are
                    kangaroo = len(user_convs)
                    return jsonify({'joined': True, 'conversations': user_convs, 'kangaroo': kangaroo})
                return jsonify({'joined': True})
    except:
        print("Could not verify user. Sending them to join page.")
        import traceback
        traceback.print_exc()
    return jsonify({'joined': False})
    
@app.route('/loadmoreconvs', methods=['POST'])
@limiter.limit("5/minute")
def loadmoreconvs():
    try:
        data = request.json
        token = data['token']
        kangaroo = data['kangaroo']
        if not check_join(token):
            return redirect('/join')
        kangaroo = int(kangaroo)
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        # get 40 more conversations, but skip the first kangaroo amount
        try:
            # load from the kangaroo amount to the kangaroo amount + 40, make sure to load from the end of the list
            user_convs = [{'conv_id': conv_id, 'name': convnames[userid][conv_id]} for conv_id in list(conversations[userid])[-(kangaroo+20):-kangaroo]]
            if len(user_convs) == 0:
                return jsonify({'noconvs': True})
        except:
            user_convs = []
        return jsonify({'conversations': user_convs, 'newkangaroo': kangaroo+40})
    except:
        return jsonify({'error': 'Could not load more conversations. Please try again later.'}), 500


@app.route('/get_convs', methods=['POST'])
@limiter.limit("5/5minute")
def get_convs():
    try:
        data = request.json
        token = data['token']
        if not check_join(token):
            return redirect('/join')
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        # get all conversations associated with the user
        try:
            user_convs = [{'conv_id': conv_id, 'name': convnames[userid][conv_id]} for conv_id in conversations[userid]]
        except:
            user_convs = []
        return jsonify({'conversations': user_convs})
    except:
        return jsonify({'error': 'Could not retrieve conversations. Please try again later.'}), 500


@app.route('/get_conv', methods=['POST'])
@limiter.limit("10/minute")
def get_conv():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        id = get_user_id(token)
        if checkBan(id):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        chat_history = conversations[id][conv_id]
        name = convnames[id][conv_id]
        chat_history_html = []
        if id in progresses and progresses[id]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        for message in copy.deepcopy(chat_history):
            if message['role'] == 'Chatbot':
                chat_history_html.append({'role': 'Chatbot', 'message': markdown2.markdown(message['message'], extras=["tables", "fenced-code-blocks", "spoiler", "strike"]), 'attachment': message['attachment'] if message.get('attachment', None) is not None else None, 'attachmentbase64': message.get('attachmentbase64', None)})
            else:
                chat_history_html.append({'role': 'User', 'message': message['message'], 'attachment': message['attachment'] if message.get('attachment', None) is not None else None, 'attachmentbase64': message.get('attachmentbase64', None)})
        conversations._save_to_disk()
        return jsonify({'chat_history': chat_history, 'chat_history_html': chat_history_html, 'name': name, 'expectedlength': len(chat_history)})
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': 'Could not retrieve conversation. Please try again later.'}), 500

@app.route('/rewind', methods=['POST'])
@limiter.limit("10/5minute")
def rewind():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        to = data['to']
        id = get_user_id(token)
        if checkBan(id):
            return jsonify({'error': 'You are banned from using the service. Please contact the system administrator (LyubomirT) for more information.'}), 403
        chat_history = conversations[id][conv_id]
        if id in progresses and progresses[id]:
            return jsonify({'error': 'Please wait for the AI to finish processing your previous message.'}), 429
        # to must be odd and greater than 0
        to = int(to)
        if to % 2 == 0 or to < 1:
            return jsonify({'error': 'Invalid rewind value.'}), 400
        if to > len(chat_history):
            return jsonify({'error': 'Invalid rewind value.'}), 400
        # Preserve the message at index 'to' by slicing up to 'to + 1'
        chat_history = chat_history[:to + 1]
        conversations[id][conv_id] = chat_history
        conversations._save_to_disk()
        return jsonify({'rewound': True, 'expectedlength': len(chat_history)})
    except Exception as e:
        return jsonify({'error': 'Could not rewind conversation. Please try again later.'}), 500



@app.route('/auth/discord')
def auth_discord():
    # if the user IP is the same as the server IP, render login.html. Otherwise, render loginprod.html
    try:
        return render_template('loginprod.html')
    except:
        return "Could not load login page. Please try again later. This means that the app is dead."

@app.route('/banned')
def render_ban():
    try:
        return render_template('banned.html')
    except:
        return "You Are Banned."

    

@app.route('/join')
def join():
    try:
        return render_template('jointos.html')
    except Exception as e:
        return "This didn't age well."

@app.route('/logout', methods=['POST', 'GET'])
def logout():
    try:
        data = request.json
        token = data['token']
        if token in savedtokens:
            del savedtokens[token]
        return jsonify({'logged_out': True})
    except:
        return jsonify({'error': "Logout failed."}), 500

@app.route('/help')
def renderhelp():
    return render_template('help.html')

# Add these error handling routes
@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(429)
@app.errorhandler(500)
def handle_error(error):
    if isinstance(error, HTTPException):
        error_code = error.code
        error_title = error.name
        error_message = error.description
    else:
        error_code = 500
        error_title = "Internal Server Error"
        error_message = "An unexpected error occurred. Please try again later."

    return render_template('error.html', error_code=error_code, error_title=error_title, error_message=error_message), error_code

@app.route('/export_conv', methods=['POST'])
@limiter.limit("10/minute")
def export_conv():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        if not check_join(token):
            return jsonify({'error': 'User not authorized'}), 401
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'User is banned'}), 403
        
        chat_history = conversations[userid][conv_id]
        
        export_text = f"Conversation ID: {conv_id}\n\n"
        for message in chat_history:
            role = message['role']
            content = message['message']
            export_text += f"{role}: {content}\n\n"
        
        return Response(
            export_text,
            mimetype="text/plain",
            headers={"Content-disposition": f"attachment; filename=conversation_{conv_id}.txt"}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/text_to_speech', methods=['POST'])
@limiter.limit("10/minute")
def text_to_speech():
    try:
        data = request.json
        text = data['text']
        token = data['token']
        if not check_join(token):
            return jsonify({'error': 'User not authorized'}), 401
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'User is banned'}), 403

        # Create a unique filename for the audio file
        filename = f"{uuid.uuid4()}.mp3"
        filepath = f"static/{filename}"  # Replace with the actual directory path

        # Generate the audio file
        tts = gTTS(text=text)
        tts.save(filepath) # won't work
        tts.save(filepath) # will work

        @after_this_request
        def remove_file(response):
            try:
                os.remove(filepath)
            except Exception as error:
                threading.Thread(target=delayFileRemoval, args=(filepath,)).start()
                print(f"Error removing file: {error}")
            return response

        # Send the file
        return send_file(filepath, mimetype="audio/mpeg")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
@app.route('/duplicate_conv', methods=['POST'])
@limiter.limit("5/minute")
def duplicate_conv():
    try:
        data = request.json
        conv_id = data['conv_id']
        token = data['token']
        if not check_join(token):
            return jsonify({'error': 'User not authorized'}), 401
        userid = get_user_id(token)
        if checkBan(userid):
            return jsonify({'error': 'User is banned'}), 403
        
        # Get the original conversation
        original_conv = conversations[userid][conv_id]
        original_name = convnames[userid][conv_id]
        
        # Generate a new conversation ID
        new_conv_id = random.randint(100000, 999999)
        
        # Create a deep copy of the conversation
        conversations[userid][new_conv_id] = copy.deepcopy(original_conv)
        
        # Create a new name for the duplicated conversation
        new_name = f"Copy of {original_name}"
        convnames[userid][new_conv_id] = new_name
        
        return jsonify({
            'new_conv_id': new_conv_id,
            'new_name': new_name
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def delayFileRemoval(filepath):
    time.sleep(10)
    os.remove(filepath)

@app.route('/privacy')
def renderprivacy():
    return render_template('privacy-policy.html')

@app.route('/terms')
def renderterms():
    return render_template('terms-of-service.html')

def trylaunchjprq():
    jprqpath = os.environ["PATH_TO_JPRQ"]
    if jprqpath is None:
        return False
    try:
        os.system(f"{jprqpath} auth {os.environ['JPRQAUTH']}")
        os.system(f"{jprqpath} http 5000 -s {os.environ['JPRQNAME']} --reconnect")
        return True
    except:
        return False
        
def launchjprqinthread():
    if not trylaunchjprq():
        print("Could not launch jprq. Please make sure that the PATH_TO_JPRQ and JPRQAUTH environment variables are set.")

# start the jprq server
t = threading.Thread(target=launchjprqinthread)
t.start()

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
