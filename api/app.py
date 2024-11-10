import gradio as gr
from cryptography.fernet import Fernet
import uuid
import logging
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import json
from groq import Groq
from gtts import gTTS
from io import BytesIO
import soundfile as sf
import numpy as np

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [SESSION: %(session_id)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class HIPAACompliantLogger:
    def __init__(self):
        self.logger = logging.getLogger('HIPAA_AUDIT')

    def log(self, session_id: str, action: str, details: dict) -> None:
        extra = {'session_id': session_id}
        self.logger.info(f"ACTION: {action} - DETAILS: {json.dumps(details)}", extra=extra)

class SecurityManager:
    def __init__(self):
        self.key = os.getenv('ENCRYPTION_KEY', Fernet.generate_key())
        self.cipher_suite = Fernet(self.key)
        self.logger = HIPAACompliantLogger()
        self.active_sessions = {}
        self.session_timeout = timedelta(minutes=15)

    def create_session(self, user_consent: bool = False) -> str:
        session_id = str(uuid.uuid4())
        self.active_sessions[session_id] = {
            'created_at': datetime.now(),
            'consent_provided': user_consent,
            'last_active': datetime.now(),
        }
        self.logger.log(session_id, "SESSION CREATED", {'consent_provided': user_consent})
        return session_id

    def encrypt_data(self, data: str) -> str:
        return self.cipher_suite.encrypt(data.encode()).decode()

    def decrypt_data(self, encrypted_data: str) -> str:
        return self.cipher_suite.decrypt(encrypted_data.encode()).decode()

    def validate_session(self, session_id: str) -> bool:
        session = self.active_sessions.get(session_id)
        if not session or datetime.now() - session['last_active'] > self.session_timeout:
            self.end_session(session_id)
            return False
        session['last_active'] = datetime.now()
        return True

    def end_session(self, session_id: str) -> bool:
        if session_id in self.active_sessions:
            self.logger.log(session_id, "SESSION ENDED", {})
            del self.active_sessions[session_id]
            return True
        return False

class SecureHealthcareTranslator:
    def __init__(self):
        self.security = SecurityManager()
        self.client = Groq()
        self.supported_languages = {
            "English": "en", "Spanish": "es", "French": "fr",
            "German": "de", "Chinese": "zh", "Arabic": "ar",
            "Hindi": "hi", "Tamil": "ta"
        }
        self.session_id = ""

        self.is_decrypted=False

    def process_audio(self, audio_input, audio_upload, source_lang, target_lang, consent):
        if not consent:
            return "Please provide consent for processing the audio.", ""

        self.session_id = self.security.create_session(consent)
        audio_path = audio_input or audio_upload
        if not audio_path:
            return "No audio input provided.", ""

        try:
            with open(audio_path, 'rb') as file:
                transcription = self.client.audio.transcriptions.create(
                    file=(audio_path, file.read()),
                    model="whisper-large-v3-turbo",
                    language=self.supported_languages[source_lang],
                    temperature=0.0
                )
            original_text = transcription.text

            prompt = f"""Translate the following medical sentence from {source_lang} to {target_lang}, preserving anatomical accuracy and medical terminology:
            "{original_text}"
            Return only the translated sentence."""
            completion = self.client.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[{"role": "system", "content": "You are a medical translator."},
                          {"role": "user", "content": prompt}],
                temperature=0.3,
            )
            translated_text = completion.choices[0].message.content

            # Encrypting sensitive data
            encrypted_original_text = self.security.encrypt_data(original_text)
            encrypted_translated_text = self.security.encrypt_data(translated_text)

            return encrypted_original_text, encrypted_translated_text

        except Exception as e:
            self.security.logger.log(self.session_id, "PROCESSING_ERROR", {"error": str(e)})
            return f"Error: {str(e)}", ""

    def text_to_speech(self, translation_text, target_lang):
        text = translation_text if self.is_decrypted else self.security.decrypt_data(translation_text)
        tts = gTTS(text=text, lang=self.supported_languages[target_lang])
        audio_fp = BytesIO()
        tts.write_to_fp(audio_fp)
        audio_fp.seek(0)
        audio_array, sample_rate = sf.read(audio_fp)
        return sample_rate, np.array(audio_array)

translator = SecureHealthcareTranslator()

def create_gradio_app():

    with gr.Blocks(theme=gr.themes.Soft()) as demo:
        with gr.Row():
            consent = gr.Checkbox(
                label="I acknowledge the HIPAA Privacy Notice and consent to processing of health information",
                value=False
            )

        with gr.Row():
            source_lang = gr.Dropdown(
                choices=list(translator.supported_languages.keys()), value="English", label="Source Language"
            )
            target_lang = gr.Dropdown(
                choices=list(translator.supported_languages.keys()), value="Spanish", label="Target Language"
            )

        with gr.Row():
            audio_input = gr.Audio(
                sources="microphone", type="filepath", label="Record Audio", streaming=True
            )
            audio_upload = gr.Audio(
                sources=["upload"], type="filepath", label="Or Upload Audio File"
            )

        with gr.Row():
            transcript = gr.Textbox(
                label="Secure Transcript", placeholder="Encrypted transcript will appear here...", lines=4, interactive=False
            )
            translation = gr.Textbox(
                label="Secure Translation", placeholder="Encrypted translation will appear here...", lines=4, interactive=False
            )

        with gr.Row():
            transcribe_button = gr.Button("Transcribe & Translate")
            clear_button = gr.Button("Clear")
            play_translation_button = gr.Button("Play Translation")
            delete_data = gr.Button("Delete My Data")
            decode_encryption=gr.Button("Decypt")

        with gr.Row():
            play_translation_audio = gr.Audio(label="Play Translated Text")

        status_message = gr.Textbox(label="Status", interactive=False)

        def handle_audio_processing(audio_input, audio_upload, source_lang, target_lang, consent):
            return translator.process_audio(audio_input, audio_upload, source_lang, target_lang, consent)

        def handle_data_deletion():
            if translator.security.end_session(translator.session_id):
                return "All health records related to the current session have been deleted."
            return "No data processed in the current session."

        def play_translation(translation_text, target_lang):
            return translator.text_to_speech(translation_text, target_lang)

        def decrypt(encrypted_transcript, encrypted_translation):

            translator.is_decrypted=True

            return translator.security.decrypt_data(encrypted_transcript), translator.security.decrypt_data(encrypted_translation)

        def clear_interface():
            return None, None, "", "", "", None

        transcribe_button.click(
            fn=handle_audio_processing,
            inputs=[audio_input, audio_upload, source_lang, target_lang, consent],
            outputs=[transcript, translation]
        )

        delete_data.click(fn=handle_data_deletion, inputs=[], outputs=[status_message])

        play_translation_button.click(
            fn=play_translation, inputs=[translation, target_lang], outputs=[play_translation_audio]
        )

        clear_button.click(
            fn=clear_interface, inputs=[], 
            outputs=[audio_input, audio_upload, transcript, translation, status_message, play_translation_audio]
        )

        decode_encryption.click(
            fn=decrypt, inputs=[transcript, translation], outputs=[transcript, translation]
        )
    return demo

app=create_gradio_app()

if __name__ == "__main__":
    app.launch(share=True)
