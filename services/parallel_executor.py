"""
Parallel API execution utility using ThreadPoolExecutor.
Enables concurrent execution of independent API calls with error handling.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)


def parallel_execute(
    tasks: Dict[str, Callable],
    max_workers: int = 5,
    timeout: int = 30
) -> Dict[str, Any]:

    results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_key = {
            executor.submit(func): key 
            for key, func in tasks.items()
        }
        
        # Process completed tasks
        for future in as_completed(future_to_key, timeout=timeout):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error(f"Task '{key}' failed: {e}")
                results[key] = None
    
    return results


def parallel_execute_list(
    tasks: List[Tuple[str, Callable]],
    max_workers: int = 5,
    timeout: int = 30
) -> Dict[str, Any]:

    return parallel_execute(dict(tasks), max_workers, timeout)
