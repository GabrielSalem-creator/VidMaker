from flask import Flask, render_template,session,send_file, request, send_from_directory, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
import os
import shutil
from moviepy import ImageSequenceClip, AudioFileClip
import requests
from huggingface_hub import InferenceClient
from datetime import *
import zipfile
import edge_tts
import asyncio
import tempfile

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///users.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)  # Optional: Session expiration
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)  # Remember login for 30 days
app.config['SESSION_PERMANENT'] = False  # Sessions won't expire unless the user logs out
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# Hugging Face API Setup
HUGGINGFACE_API_KEY = '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'
headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}

# Play.ht API Setup
PLAYHT_API_KEY = 'aaa4d615220c4491a52daaff02576fa7'
PLAYHT_USER_ID = 'Dh8IKiblhTgy08RI4IPV6uiovdb2'
playht_headers = {
    "Authorization": f"Bearer {PLAYHT_API_KEY}",
    "X-User-ID": PLAYHT_USER_ID
}

# User Model with only the necessary columns
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False)

    generations_left = db.Column(db.Integer, default=10)  # Store the number of generations left
    last_reset = db.Column(db.DateTime, default=datetime.utcnow)  # Track when the generation limit was last reset



def reset_weekly_limit(user):
    """Reset the user's generation limit every week."""
    current_time = datetime.utcnow()
    time_difference = current_time - user.last_reset

    # If more than a week has passed since the last reset, reset the generation count
    if time_difference >= timedelta(weeks=1):
        user.generations_left = 10  # Reset the counter to 10
        user.last_reset = current_time  # Update the last reset time
        db.session.commit()


def get_user_credits():
    """Get the number of credits left for the logged-in user."""
    if current_user.is_authenticated:
        return current_user.generations_left  # Return the number of remaining generations
    return 0  # If the user is not logged in, return 0 credits



# Route to download database
@app.route("/givemedata")
def download_database():
    return send_file("instance/users.db", as_attachment=True)

# Route to download videos directory
@app.route("/givemevideo")
def download_videos():

    zip_path = "videos.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk("static/videos"):
            for file in files:
                zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), "static/videos"))

    return send_file(zip_path, as_attachment=True)

# Load user for login sessions
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def is_vpn(ip_address):
    api_key = "9e33ead3dae942948aa834c66960dd48"  # Replace with your VPN detection API key
    url = f"https://vpnapi.io/api/{ip_address}?key={api_key}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get("security", {}).get("vpn", False)  # Returns True if VPN is detected
    return False

def get_client_ip(request):
    """Returns the real client IP address, considering proxy headers."""
    if 'X-Forwarded-For' in request.headers:
        ip_address = request.headers['X-Forwarded-For'].split(',')[0]
    else:
        ip_address = request.remote_addr
    return ip_address

def generate_voice_options():
    """
    Generate HTML options for all available Edge TTS voices.

    Returns:
        str: HTML string containing voice options.
    """
    async def get_voices():
        voices = await edge_tts.list_voices()
        return {f"{v['ShortName']} - {v['Locale']} ({v['Gender']})": v['ShortName'] for v in voices}

    voices = asyncio.run(get_voices())

    options_html = ""
    for display_name, short_name in voices.items():
        options_html += f'<option value="{short_name}">{display_name}</option>'
    return options_html

def generate_speech(user_story, selected_voice, rate=0, pitch=0):
    """
    Generate speech from the provided text and voice using Edge TTS.

    Args:
        user_story (str): The text to convert to speech.
        selected_voice (str): The voice short name to use for TTS.
        rate (int): The speech rate adjustment (default is 0).
        pitch (int): The pitch adjustment (default is 0).

    Returns:
        None
    """
    try:
        # Run the async text-to-speech function
        audio_path = asyncio.run(text_to_speech(user_story, selected_voice, rate, pitch))

        # Move the generated audio file to the desired location
        output_path = os.path.join("static", "monologue_voice.mp3")
        os.replace(audio_path, output_path)

        print(f"Audio file saved as {output_path}")
    except Exception as e:
        print(f"Error: {e}")

async def text_to_speech(text, voice, rate=0, pitch=0):
    """
    Convert text to speech using Edge TTS.

    Args:
        text (str): The text to convert.
        voice (str): The short name of the selected voice.
        rate (int): The speech rate adjustment (percentage).
        pitch (int): The pitch adjustment (Hz).

    Returns:
        str: Path to the generated audio file.
    """
    if not text.strip():
        raise ValueError("Text cannot be empty.")

    if not voice:
        raise ValueError("Voice must be specified.")

    rate_str = f"{rate:+d}%"
    pitch_str = f"{pitch:+d}Hz"

    communicate = edge_tts.Communicate(text, voice, rate=rate_str, pitch=pitch_str)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
        tmp_path = tmp_file.name
        await communicate.save(tmp_path)

    return tmp_path


@app.route("/")
def main():
    user_is_logged_in = current_user.is_authenticated  # Check if the user is logged in
    return render_template("main.html", user=current_user, user_is_logged_in=user_is_logged_in)

@app.route("/tool", methods=['GET', 'POST'])
@login_required
def tool():
    video_path = None
    user = current_user
    credits_left = get_user_credits()

    # Reset the weekly limit if necessary
    reset_weekly_limit(user)

    voice_options = generate_voice_options()  # Generate voice options dynamically

    if request.method == 'POST':
        if user.generations_left <= 0:
            flash("You have used all your credits for this week. Please try again later.", "error")
            return render_template('index.html', video_path=None, credits_left=credits_left, voice_options=voice_options)

        try:
            user_story = request.form['story']
            selected_voice = request.form['voice']
            titles, sentences = generate_titles_and_sentences(user_story)

            if not titles:
                print("No titles generated from story.")
                return render_template('index.html', video_path=None, credits_left=credits_left, voice_options=voice_options)

            # Generate speech from the user's story
            generate_speech(user_story, selected_voice)

            # Generate images for each title
            image_paths = generate_images(titles, "static/images")

            if not image_paths:
                print("No images generated.")
                return render_template('index.html', video_path=None, credits_left=credits_left, voice_options=voice_options)

            # Create the video
            video_file = os.path.join("static", "output_video.mp4")
            create_video(image_paths, video_file)

            # Clean up images after video creation
            for i in range(len(image_paths)):
                os.remove("static/images/image_" + str(i + 1) + ".png")

            # Save the video with a unique name
            target_directory = "static/videos"
            base_filename = "output_video"
            extension = ".mp4"
            counter = 0

            output_filename = f"{base_filename}{counter}{extension}"
            output_path = os.path.join(target_directory, output_filename)

            while os.path.exists(output_path):
                counter += 1
                output_filename = f"{base_filename}{counter}{extension}"
                output_path = os.path.join(target_directory, output_filename)

            shutil.copy(video_file, output_path)
            video_path = output_path

            # Decrement the user's remaining generation credits
            user.generations_left -= 1
            db.session.commit()

        except Exception as e:
            print("Error occurred:", str(e))
            video_path = None

    return render_template('index.html', video_path=video_path, credits_left=credits_left, voice_options=voice_options)



@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(request.form["password"]).decode("utf-8")
        ip_address = get_client_ip(request)

        # Block VPN users (Example logic, define `is_vpn()` as needed)
        if is_vpn(ip_address):
            error_message = "Signups from VPNs are not allowed."
            return render_template("auth.html", error=error_message)

        # Check if an account with the same IP address already exists
        existing_user_by_ip = User.query.filter_by(ip_address=ip_address).first()
        if existing_user_by_ip:
            error_message = "An account has already been created from this IP address."
            return render_template("auth.html", error=error_message)

        # Check if the email is already in use
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            error_message = "Email already used. Please try another one."
            return render_template("auth.html", error=error_message)

        # If all checks pass, create the user
        user = User(name=name, email=email, password=password, ip_address=ip_address)
        db.session.add(user)
        db.session.commit()

        # Log the user in after successful signup with remember option
        login_user(user, remember=True)  # Remember user session
        return redirect(url_for("tool"))

    return render_template("auth.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        ip_address = get_client_ip(request)

        user = User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password, password):
            # Log the user in with remember option
            login_user(user, remember=True)
            return redirect(url_for("tool"))
        else:
            error_message = "Incorrect email or password."
            return render_template("auth.html", error=error_message)

    return render_template("auth.html")

@app.route("/videos")
def videos():
    video_dir = 'static/videos/'
    videos = []

    # Iterate over all files in the directory
    for filename in os.listdir(video_dir):
        if filename.endswith('.mp4'):  # Filter only .mp4 files
            file_path = os.path.join(video_dir, filename)
            created_on = datetime.fromtimestamp(os.path.getctime(file_path)).strftime('%B %d, %Y')

            videos.append({
                'path': f'/static/videos/{filename}',
                'created_on': created_on
            })

    # Render the HTML template with video data
    return render_template('videos.html', videos=videos)


@app.route('/logout')
@login_required
def logout():
    logout_user()  # This will clear the session and log out the user
    flash("You have been logged out successfully.", "success")
    return redirect(url_for("login"))

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# Function to generate titles and sentences
def generate_titles_and_sentences(user_story):
    sentences = user_story.split('.')
    titles = [f"Title for: {s.strip()}" for s in sentences if s.strip()]  # Create titles for each sentence
    return titles, sentences

# Function to generate images for each title
def generate_images(titles, output_dir):
    client = InferenceClient(model="stabilityai/stable-diffusion-3.5-large-turbo", token="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")
    os.makedirs(output_dir, exist_ok=True)
    image_paths = []
    for i, title in enumerate(titles):
        image = client.text_to_image(title)
        image_path = os.path.join(output_dir, f"image_{i + 1}.png")
        image.save(image_path)
        image_paths.append(image_path)
    return image_paths

# Function to generate speech from the user's story


# Function to create a video from images and audio
def create_video(image_paths, output_file):
    if not image_paths:
        print("No images to create video.")
        return

    audio = AudioFileClip("static/monologue_voice.mp3")
    audio_duration = audio.duration
    fps = len(image_paths) / audio_duration if audio_duration > 0 else 1
    clip = ImageSequenceClip(image_paths, fps=fps)
    clip = clip.with_audio(audio)
    clip.write_videofile(output_file, codec="libx264", audio_codec="aac")
    audio.close()


# Run the application
if __name__ == "__main__":
    app.run(debug=True, port=8000)

