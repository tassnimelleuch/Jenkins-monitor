#!/usr/bin/env python3
"""
Utility to delete old Jenkins builds and keep only recent ones.
Usage: python3 clean_builds.py --keep 20 --dry-run
"""

import requests
import argparse
from config import Config

def _get_auth():
    return (Config.JENKINS_USERNAME, Config.JENKINS_TOKEN)

def _get_base():
    url = Config.JENKINS_URL.rstrip('/')
    job = Config.JENKINS_JOB
    return f"{url}/job/{job}"

def _get_root():
    return Config.JENKINS_URL.rstrip('/')

def _is_multibranch():
    """Check if pipeline is multibranch."""
    try:
        resp = requests.get(
            f'{_get_base()}/api/json?tree=_class',
            auth=_get_auth(),
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return 'MultiBranch' in data.get('_class', '')
    except Exception:
        pass
    return False

def _get_multibranch_branches():
    """Get all branches from multibranch pipeline."""
    try:
        resp = requests.get(
            f'{_get_base()}/api/json?tree=jobs[name]',
            auth=_get_auth(),
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            return [j.get('name') for j in data.get('jobs', []) if j.get('name')]
    except Exception as e:
        print(f"Error fetching branches: {e}")
    return []

def get_builds(job_path):
    """Get all builds from a job."""
    try:
        resp = requests.get(
            f'{job_path}/api/json?tree=builds[number]',
            auth=_get_auth(),
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            builds = data.get('builds', [])
            # Sort by number, descending (newest first)
            return sorted([b.get('number') for b in builds], reverse=True)
    except Exception as e:
        print(f"Error fetching builds from {job_path}: {e}")
    return []

def delete_build(job_path, build_number, dry_run=True):
    """Delete a specific build."""
    url = f'{job_path}/{build_number}/doDelete'
    
    if dry_run:
        print(f"  [DRY RUN] Would delete: {job_path}/{build_number}")
        return True
    
    try:
        resp = requests.post(
            url,
            auth=_get_auth(),
            timeout=10
        )
        if resp.status_code in (200, 204, 302):
            print(f"  ✓ Deleted: {build_number}")
            return True
        else:
            print(f"  ✗ Failed to delete {build_number}: HTTP {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ Error deleting {build_number}: {e}")
        return False

def clean_builds(keep=20, dry_run=True):
    """Delete old builds, keeping only the most recent ones."""
    print(f"\n{'='*60}")
    print(f"Jenkins Build Cleaner")
    print(f"Pipeline: {Config.JENKINS_JOB}")
    print(f"Keep: {keep} most recent builds")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print(f"{'='*60}\n")
    
    if _is_multibranch():
        print("Multibranch pipeline detected\n")
        branches = _get_multibranch_branches()
        print(f"Found {len(branches)} branches: {branches}\n")
        
        total_deleted = 0
        for branch in branches:
            job_path = f'{_get_base()}/job/{branch}'
            print(f"Branch: {branch}")
            builds = get_builds(job_path)
            
            if not builds:
                print("  No builds found\n")
                continue
            
            print(f"  Total builds: {len(builds)}")
            
            if len(builds) > keep:
                to_delete = builds[keep:]  # Keep the first `keep` builds (most recent)
                print(f"  Will delete: {len(to_delete)} builds")
                print(f"  Keeping builds: {builds[:keep]}")
                print(f"  Deleting builds: {to_delete}\n")
                
                for build_num in to_delete:
                    delete_build(job_path, build_num, dry_run)
                    total_deleted += 1
            else:
                print(f"  No builds to delete (only {len(builds)} builds, keeping {keep})\n")
        
        print(f"\n{'='*60}")
        print(f"Total builds marked for deletion: {total_deleted}")
        if dry_run:
            print("(Run with --execute to actually delete)")
        print(f"{'='*60}\n")
    else:
        print("Single-branch pipeline detected\n")
        job_path = _get_base()
        builds = get_builds(job_path)
        
        if not builds:
            print("No builds found\n")
            return
        
        print(f"Total builds: {len(builds)}")
        
        if len(builds) > keep:
            to_delete = builds[keep:]
            print(f"Will delete: {len(to_delete)} builds")
            print(f"Keeping builds: {builds[:keep]}")
            print(f"Deleting builds: {to_delete}\n")
            
            for build_num in to_delete:
                delete_build(job_path, build_num, dry_run)
        else:
            print(f"No builds to delete (only {len(builds)} builds, keeping {keep})\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Clean old Jenkins builds')
    parser.add_argument('--keep', type=int, default=20, help='Number of recent builds to keep (default: 20)')
    parser.add_argument('--execute', action='store_true', help='Actually delete builds (default is dry-run)')
    
    args = parser.parse_args()
    
    clean_builds(keep=args.keep, dry_run=not args.execute)
