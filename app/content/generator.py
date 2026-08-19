"""
AUTO Video Maker - Content Generator

Generates production plans from content briefs using LLM providers.
"""

import json
import re
import time
import logging
from typing import Optional

from app.content.models import (
    ContentBrief,
    ProductionPlan,
    ContentGenerationResult,
    generate_project_id,
)
from app.content.providers.base import BaseLLMProvider
from app.content.prompts.scene_planner import get_combined_generation_prompt
from app.content.validator import validate_production_plan
from app.content.config import MAX_RETRY_ATTEMPTS, RETRY_DELAY
from app.content.exceptions import (
    LLMProviderError,
    LLMAuthenticationError,
    LLMQuotaError,
    LLMResponseError,
    RetryExhaustedError,
)

logger = logging.getLogger(__name__)


class ContentGenerator:
    """
    Generates production plans from content briefs.
    
    Uses an LLM provider to generate structured content.
    """
    
    def __init__(
        self,
        provider: BaseLLMProvider,
        max_retries: int = MAX_RETRY_ATTEMPTS,
        retry_delay: float = RETRY_DELAY,
    ):
        self.provider = provider
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    def generate(self, brief: ContentBrief) -> ContentGenerationResult:
        """
        Generate a production plan from a content brief.
        
        Args:
            brief: The content brief specifying what to create.
        
        Returns:
            ContentGenerationResult with the plan or error.
        """
        logger.info(f"Generating content for topic: {brief.topic}")
        logger.info(f"Provider: {self.provider.name}, Model: {self.provider.model}")
        
        # Get prompts
        system_prompt, user_prompt = get_combined_generation_prompt(brief)
        
        last_error = None
        attempts = 0
        
        for attempt in range(1, self.max_retries + 1):
            attempts = attempt
            logger.info(f"Generation attempt {attempt}/{self.max_retries}")
            
            try:
                # Call LLM
                response = self.provider.generate_json(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.7,
                    max_tokens=4096,
                )
                
                # Parse JSON response
                plan = self._parse_response(response.content, brief)
                
                # Validate plan
                validation = validate_production_plan(plan, brief)
                
                if not validation.valid:
                    # Validation failed - retry if possible
                    error_msg = "; ".join(validation.errors)
                    logger.warning(f"Validation failed: {error_msg}")
                    last_error = error_msg
                    
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    else:
                        return ContentGenerationResult.failure(
                            error=f"Validation failed after {attempts} attempts: {error_msg}",
                            provider=self.provider.name,
                            model=self.provider.model,
                            attempts=attempts,
                        )
                
                # Success
                logger.info(f"Generation successful on attempt {attempt}")
                return ContentGenerationResult.from_plan(
                    plan=plan,
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                    warnings=validation.warnings,
                )
                
            except LLMAuthenticationError as e:
                # Don't retry auth errors
                logger.error(f"Authentication error: {e}")
                return ContentGenerationResult.failure(
                    error=str(e),
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                )
            
            except LLMQuotaError as e:
                # Don't retry quota errors
                logger.error(f"Quota error: {e}")
                return ContentGenerationResult.failure(
                    error=str(e),
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                )
            
            except (LLMResponseError, json.JSONDecodeError) as e:
                # Retry on response/parsing errors
                logger.warning(f"Response error on attempt {attempt}: {e}")
                last_error = str(e)
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
            
            except LLMProviderError as e:
                # Retry on transient provider errors
                logger.warning(f"Provider error on attempt {attempt}: {e}")
                last_error = str(e)
                
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
            
            except Exception as e:
                # Unexpected error - don't retry
                logger.exception(f"Unexpected error: {e}")
                return ContentGenerationResult.failure(
                    error=f"Unexpected error: {e}",
                    provider=self.provider.name,
                    model=self.provider.model,
                    attempts=attempts,
                )
        
        # All retries exhausted
        return ContentGenerationResult.failure(
            error=f"All {attempts} attempts failed. Last error: {last_error}",
            provider=self.provider.name,
            model=self.provider.model,
            attempts=attempts,
        )
    
    def _parse_response(self, content: str, brief: ContentBrief) -> ProductionPlan:
        """
        Parse LLM response into ProductionPlan.
        
        Args:
            content: Raw LLM response content.
            brief: Original content brief.
        
        Returns:
            Parsed ProductionPlan.
        
        Raises:
            LLMResponseError: If parsing fails.
        """
        # Clean up response
        content = content.strip()
        
        # Remove markdown code blocks if present
        if content.startswith("```"):
            # Find the end of the code block
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # Remove opening ```json or ```
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # Remove closing ```
            content = "\n".join(lines)
        
        # Try to extract JSON from response
        content = content.strip()
        
        # Find JSON object boundaries
        start = content.find("{")
        end = content.rfind("}") + 1
        
        if start == -1 or end == 0:
            raise LLMResponseError("No JSON object found in response", content[:500])
        
        json_str = content[start:end]
        
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise LLMResponseError(f"Invalid JSON: {e}", json_str[:500])
        
        # Ensure project_id exists
        if "project_id" not in data or not data["project_id"]:
            data["project_id"] = generate_project_id(brief.topic)
        
        # Parse into Pydantic model
        try:
            plan = ProductionPlan(**data)
        except Exception as e:
            raise LLMResponseError(f"Schema validation failed: {e}", json_str[:500])
        
        return plan
