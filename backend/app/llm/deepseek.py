import httpx
from app.core.config import settings

class DeepSeekClient:
    def chat_text(self, system: str, user: str) -> str:
        if not settings.deepseek_api_key:
            raise RuntimeError('MOCK_MODE=false 时必须配置 DEEPSEEK_API_KEY')
        payload={
            'model':settings.deepseek_model,
            'messages':[{'role':'system','content':system},{'role':'user','content':user}],
            'temperature':0.2,
        }
        with httpx.Client(timeout=settings.deepseek_timeout_seconds) as c:
            r=c.post(settings.deepseek_base_url.rstrip('/')+'/chat/completions',headers={'Authorization':f'Bearer {settings.deepseek_api_key}','Content-Type':'application/json'},json=payload)
            r.raise_for_status()
            return r.json()['choices'][0]['message']['content']
client=DeepSeekClient()
