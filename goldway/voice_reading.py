"""Bounded, cached voice transcription; audio is data, not task authority."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave
import httpx

MAX_BYTES=8*1024*1024
MAX_SECONDS=180
RESERVATION=0.02  # Above three minutes at published $0.0045/min; shared daily cap.


def attachment(message):
    a=message.get('voice') or message.get('audio')
    if not isinstance(a,dict):return None
    if not isinstance(a.get('file_id'),str) or not 0<len(a['file_id'])<=512:return None
    if type(a.get('duration')) not in (int,float) or not 0<a['duration']<=MAX_SECONDS:return None
    if a.get('file_size') is not None and (type(a['file_size']) is not int or not 0<a['file_size']<=MAX_BYTES):return None
    return {'file_id':a['file_id']}


def pcm(body):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BYTES:raise ValueError('voice_size')
    with tempfile.TemporaryDirectory(prefix='goldway-voice-') as d:
        src=Path(d)/'input';dst=Path(d)/'audio.wav';src.write_bytes(body)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','ogg,mp3,wav,mov,matroska,webm','-i',str(src),'-t','181','-vn','-ac','1','-ar','16000','-threads','1','-f','wav',str(dst)],check=True,timeout=25,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if dst.stat().st_size>6*1024*1024:raise ValueError('voice_size')
        data=dst.read_bytes()
    with wave.open(io.BytesIO(data),'rb') as w:
        duration=w.getnframes()/w.getframerate()
        if not 0<duration<=MAX_SECONDS or w.getnchannels()!=1 or w.getframerate()!=16000:raise ValueError('voice_duration')
    return data


def recognize(work,body):
    model=work.e.model;c=model.config;s=work.s;e=work.e
    if c['provider']!='openai':raise ValueError('voice_provider')
    audio=pcm(body)
    with s.transaction():
        spent=s.execute('SELECT COALESCE(SUM(reserved_cost),0) FROM usage WHERE business=? AND at>=?',(e.b,e.now()-86400)).fetchone()[0]
        if spent+RESERVATION>c['daily_budget']:raise ValueError('Daily model budget reached')
        s.execute('INSERT INTO usage(business,at,provider,input_tokens,output_tokens,reserved_cost) VALUES(?,?,?,?,?,?)',(e.b,e.now(),'openai_transcription',0,0,RESERVATION))
    # Fixed official endpoint: never send audio to arbitrary base URLs or redirects.
    with httpx.Client(timeout=60,follow_redirects=False) as client:
        response=client.post('https://api.openai.com/v1/audio/transcriptions',headers={'Authorization':'Bearer '+model.key},data={'model':'gpt-transcribe'},files={'file':('audio.wav',audio,'audio/wav')})
        response.raise_for_status();text=response.json().get('text')
    if not isinstance(text,str) or not text.strip() or len(text)>6000:raise ValueError('voice_transcript')
    return text.strip()


def transcribe(work,message):
    if not message.get('voice'):return
    if message.get('voice_state')=='done':return
    if message.get('voice_state') in ('reading','failed'):raise ValueError('voice_fresh_submission_required')
    def save():
        work.s.execute('UPDATE group_work_events SET payload=? WHERE business=? AND chat_id=? AND message_id=?',(json.dumps(message,ensure_ascii=False),work.e.b,work.c['chat_id'],message['id']))
    message['voice_state']='reading';save()
    try:
        body=work.d.api.voice(message['voice']['file_id'])
        if not work.current():raise ValueError('voice_revoked')
        text=recognize(work,body)
        if not work.current():raise ValueError('voice_revoked')
        message['voice_reading']=text;message['voice_state']='done';save()
    except Exception:
        message['voice_state']='failed';save();raise
