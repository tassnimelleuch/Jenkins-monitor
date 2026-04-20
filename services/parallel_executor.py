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
    """
    Execute multiple independent tasks in parallel.
    
    Args:
        tasks: Dictionary mapping task names to callable functions.
               Example: {'jenkins': func1, 'github': func2, 'prometheus': func3}
        max_workers: Maximum number of threads to use (default: 5)
        timeout: Timeout per task in seconds (default: 30)
    
    Returns:
        Dictionary with same keys as input, containing results or None if failed.
        Exceptions are logged and None is returned for failed tasks.
    
    Example:
        results = parallel_execute({
            'jenkins': lambda: get_all_builds(),
            'github': lambda: get_repo(owner, repo),
            'prometheus': lambda: query_metrics()
        })
        # results = {'jenkins': [...], 'github': {...}, 'prometheus': {...}}
    """
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
    """
    Execute multiple independent tasks in parallel (preserves order via list).
    
    Args:
        tasks: List of (name, callable) tuples.
               Example: [('jenkins', func1), ('github', func2)]
        max_workers: Maximum number of threads to use
        timeout: Timeout per task in seconds
    
    Returns:
        Dictionary with task names as keys and results as values.
    """
    return parallel_execute(dict(tasks), max_workers, timeout)
