from typing import List, Dict, Any, Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from loguru import logger
from config.config import settings


class LLMClient:
    """
    LLM客户端连接模块
    """
    
    def __init__(self, model_name: Optional[str] = None, temperature: Optional[float] = None):
        """
        初始化LLM客户端
        
        Args:
            model_name (str): 模型名称，默认取配置 settings.LLM_MODEL
            temperature (float): 采样温度，默认取配置 settings.LLM_TEMPERATURE
        """
        model_name = model_name or settings.LLM_MODEL
        temperature = settings.LLM_TEMPERATURE if temperature is None else temperature

        # 获取 LLM 的 API 密钥和基础 URL（环境变量优先，回退到配置）
        # Always use the resolved HireMS settings.  Reading generic process-level
        # LLM_* variables here would bypass the project-specific collision guard.
        api_key = settings.LLM_API_KEY
        base_url = settings.LLM_BASE_URL

        if not api_key:
            raise ValueError("HIREMS_LLM_API_KEY (or compatible LLM_API_KEY) environment variable is not set")
        
        # 初始化模型
        self.model = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            openai_api_key=api_key,
            openai_api_base=base_url
        )
        self.model_name = model_name
        logger.info(f"Initialized LLM client with model: {model_name}")

    def generate_text(
        self, prompt: str, system_message: Optional[str] = None, max_tokens: Optional[int] = None
    ) -> str:
        """
        生成文本
        
        Args:
            prompt (str): 用户提示
            system_message (str, optional): 系统消息
            
        Returns:
            str: 生成的文本
        """
        try:
            if system_message:
                messages = [
                    SystemMessage(content=system_message),
                    HumanMessage(content=prompt)
                ]
            else:
                messages = [HumanMessage(content=prompt)]
                
            model = self.model.bind(max_tokens=max_tokens) if max_tokens else self.model
            response = model.invoke(messages)
            content = self._response_text(response)

            # Some OpenAI-compatible endpoints accept max_tokens but respond with an
            # empty completion. Retry once without the optional parameter so a report
            # never silently turns into an empty UI field.
            if not content and max_tokens:
                logger.warning("LLM returned empty content with max_tokens; retrying without it")
                content = self._response_text(self.model.invoke(messages))

            logger.debug(f"Generated text with {len(content)} characters")
            return content
        except Exception as e:
            logger.error(f"Failed to generate text: {e}")
            raise

    @staticmethod
    def _response_text(response: Any) -> str:
        """Normalize string and structured OpenAI-compatible message content."""
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            ]
            return "".join(parts).strip()
        return str(content or "").strip()

    def generate_with_template(self, template: str, **kwargs) -> str:
        """
        使用模板生成文本
        
        Args:
            template (str): 提示模板
            **kwargs: 模板变量
            
        Returns:
            str: 生成的文本
        """
        try:
            prompt = ChatPromptTemplate.from_template(template)
            chain = prompt | self.model | StrOutputParser()
            response = chain.invoke(kwargs)
            logger.debug(f"Generated text with template, response length: {len(response)}")
            return response
        except Exception as e:
            logger.error(f"Failed to generate text with template: {e}")
            raise
