"""
ScamHound BubbleMaps API Client
Token holder clustering and decentralization analysis
"""

import os
import requests
from typing import Optional, Dict, List
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.bubblemaps.io"

# Supported chains
SUPPORTED_CHAINS = [
    "eth", "base", "solana", "tron", "bsc", 
    "apechain", "ton", "polygon", "avalanche", 
    "sonic", "monad", "aptos"
]


def _make_request(endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Make an authenticated request to the BubbleMaps API."""
    api_key = os.environ.get("BUBBLEMAPS_API_KEY", "")
    if not api_key:
        logger.error("[BUBBLEMAPS] API key not configured")
        return None
    
    headers = {
        "X-ApiKey": api_key,
        "Content-Type": "application/json"
    }
    
    url = f"{BASE_URL}{endpoint}"
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"[BUBBLEMAPS] API error on {endpoint}: {e}")
        return None


def get_supported_chains() -> List[str]:
    """Return list of supported blockchain chains."""
    return SUPPORTED_CHAINS.copy()


def get_map_data(token_address: str, chain: str = "solana") -> Optional[Dict]:
    """
    Get full map data for a token.
    
    Args:
        token_address: Token contract address
        chain: Blockchain chain (default: solana)
        
    Returns:
        Map data including nodes, links, and metadata
    """
    if chain not in SUPPORTED_CHAINS:
        logger.error(f"[BUBBLEMAPS] Unsupported chain: {chain}")
        return None
    
    endpoint = f"/v1/map/{chain}/{token_address}"
    return _make_request(endpoint)


def get_cluster_analysis(token_address: str, chain: str = "solana") -> Optional[Dict]:
    """
    Get cluster analysis for a token - key data for risk scoring.
    
    Args:
        token_address: Token contract address
        chain: Blockchain chain (default: solana)
        
    Returns:
        Cluster analysis data including:
        - decentralization_score (0-100, higher = more decentralized)
        - cluster_count (number of distinct clusters)
        - largest_cluster_share (percentage held by largest cluster)
        - risk_signal (e.g., "HIGHLY_CENTRALIZED", "MODERATE", "DECENTRALIZED")
    """
    map_data = get_map_data(token_address, chain)
    if not map_data:
        return None
    
    try:
        # Extract nodes and calculate clustering metrics
        nodes = map_data.get("nodes", [])
        links = map_data.get("links", [])
        
        if not nodes:
            logger.warning(f"[BUBBLEMAPS] No nodes found for {token_address[:8]}...")
            return None
        
        # Calculate total supply from nodes
        total_supply = sum(node.get("amount", 0) for node in nodes)
        if total_supply == 0:
            logger.warning(f"[BUBBLEMAPS] Zero total supply for {token_address[:8]}...")
            return None
        
        # Identify clusters using connected component analysis
        # Build adjacency list from links
        adjacency = {}
        for node in nodes:
            adjacency[node.get("id", "")] = set()
        
        for link in links:
            source = link.get("source", "")
            target = link.get("target", "")
            if source in adjacency and target in adjacency:
                adjacency[source].add(target)
                adjacency[target].add(source)
        
        # Find connected components (clusters) using BFS
        visited = set()
        clusters = []
        
        for node_id in adjacency:
            if node_id not in visited:
                # BFS to find all nodes in this cluster
                cluster_nodes = []
                queue = [node_id]
                visited.add(node_id)
                
                while queue:
                    current = queue.pop(0)
                    # Find node data
                    node_data = next((n for n in nodes if n.get("id") == current), None)
                    if node_data:
                        cluster_nodes.append(node_data)
                    
                    # Add unvisited neighbors
                    for neighbor in adjacency.get(current, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                
                if cluster_nodes:
                    clusters.append(cluster_nodes)
        
        # Calculate cluster metrics
        cluster_count = len(clusters)
        
        # Calculate holdings per cluster
        cluster_holdings = []
        for cluster in clusters:
            cluster_amount = sum(node.get("amount", 0) for node in cluster)
            cluster_pct = (cluster_amount / total_supply) * 100 if total_supply > 0 else 0
            cluster_holdings.append(cluster_pct)
        
        # Sort by size (descending)
        cluster_holdings.sort(reverse=True)
        largest_cluster_share = cluster_holdings[0] if cluster_holdings else 0
        
        # Calculate decentralization score (0-100, higher = better)
        # Based on: number of clusters, distribution of holdings
        if cluster_count == 1:
            decentralization_score = max(0, 100 - int(largest_cluster_share))
        else:
            # More clusters and more even distribution = higher score
            base_score = min(100, cluster_count * 15)  # 15 points per cluster, max 100
            
            # Penalize if one cluster dominates
            if largest_cluster_share > 50:
                base_score -= 30
            elif largest_cluster_share > 30:
                base_score -= 15
            
            decentralization_score = max(0, min(100, base_score))
        
        # Determine risk signal
        if largest_cluster_share > 70 or cluster_count == 1:
            risk_signal = "HIGHLY_CENTRALIZED"
        elif largest_cluster_share > 40 or cluster_count < 3:
            risk_signal = "MODERATE_CENTRALIZATION"
        elif cluster_count >= 5 and largest_cluster_share < 25:
            risk_signal = "DECENTRALIZED"
        else:
            risk_signal = "MODERATE"
        
        return {
            "decentralization_score": decentralization_score,
            "cluster_count": cluster_count,
            "largest_cluster_share": round(largest_cluster_share, 2),
            "risk_signal": risk_signal,
            "cluster_holdings_pct": [round(pct, 2) for pct in cluster_holdings[:5]],
            "total_nodes": len(nodes),
            "total_links": len(links)
        }
        
    except Exception as e:
        logger.error(f"[BUBBLEMAPS] Error analyzing clusters for {token_address[:8]}...: {e}")
        return None


def get_holder_connections(token_address: str, chain: str = "solana") -> Optional[Dict]:
    """
    Get connection data between token holders.
    
    Args:
        token_address: Token contract address
        chain: Blockchain chain (default: solana)
        
    Returns:
        Connection data showing relationships between holders
    """
    map_data = get_map_data(token_address, chain)
    if not map_data:
        return None
    
    nodes = map_data.get("nodes", [])
    links = map_data.get("links", [])
    
    return {
        "holder_count": len(nodes),
        "connection_count": len(links),
        "nodes": nodes,
        "links": links
    }


def analyze_token_distribution(token_address: str, chain: str = "solana") -> Optional[Dict]:
    """
    Comprehensive token distribution analysis.
    
    Args:
        token_address: Token contract address
        chain: Blockchain chain (default: solana)
        
    Returns:
        Complete distribution analysis including:
        - Gini coefficient approximation
        - Concentration metrics
        - Cluster analysis
        - Risk assessment
    """
    map_data = get_map_data(token_address, chain)
    if not map_data:
        return None
    
    try:
        nodes = map_data.get("nodes", [])
        if not nodes:
            return None
        
        # Get cluster analysis
        cluster_analysis = get_cluster_analysis(token_address, chain)
        
        # Calculate holdings distribution
        holdings = [node.get("amount", 0) for node in nodes]
        total_supply = sum(holdings)
        
        if total_supply == 0:
            return None
        
        # Sort holdings descending
        holdings.sort(reverse=True)
        
        # Calculate concentration percentages
        top_1_pct = (holdings[0] / total_supply) * 100 if holdings else 0
        top_5_pct = (sum(holdings[:5]) / total_supply) * 100 if len(holdings) >= 5 else 100
        top_10_pct = (sum(holdings[:10]) / total_supply) * 100 if len(holdings) >= 10 else 100
        
        # Simple Gini coefficient approximation
        n = len(holdings)
        if n > 1:
            cumulative = 0
            for i, h in enumerate(holdings):
                cumulative += (i + 1) * h
            gini = (2 * cumulative) / (n * total_supply) - (n + 1) / n
            gini = max(0, min(1, gini))  # Clamp to [0, 1]
        else:
            gini = 1.0  # Perfect inequality with one holder
        
        # Risk assessment
        risk_factors = []
        
        if top_1_pct > 50:
            risk_factors.append("Single holder controls majority supply")
        elif top_1_pct > 30:
            risk_factors.append("High concentration in top holder")
        
        if top_5_pct > 80:
            risk_factors.append("Top 5 holders control >80% of supply")
        
        if gini > 0.8:
            risk_factors.append("Extreme wealth inequality (Gini > 0.8)")
        
        if cluster_analysis:
            if cluster_analysis.get("risk_signal") == "HIGHLY_CENTRALIZED":
                risk_factors.append("Wallet clustering detected - possible coordinated control")
        
        return {
            "total_holders": len(nodes),
            "total_supply": total_supply,
            "top_1_pct": round(top_1_pct, 2),
            "top_5_pct": round(top_5_pct, 2),
            "top_10_pct": round(top_10_pct, 2),
            "gini_coefficient": round(gini, 3),
            "cluster_analysis": cluster_analysis,
            "risk_factors": risk_factors,
            "risk_level": "HIGH" if len(risk_factors) >= 2 else "MEDIUM" if risk_factors else "LOW"
        }
        
    except Exception as e:
        logger.error(f"[BUBBLEMAPS] Error analyzing distribution for {token_address[:8]}...: {e}")
        return None
