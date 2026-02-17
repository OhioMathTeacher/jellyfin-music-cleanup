"""
Duplicate Artist Finder

Uses fuzzy string matching to find artists that are likely duplicates.
"""

import re
from dataclasses import dataclass, field
from collections import defaultdict

from rapidfuzz import fuzz
from jellyfin_client import ArtistInfo


@dataclass
class DuplicateGroup:
    """A group of artists that are likely duplicates"""
    canonical_name: str
    artists: list[ArtistInfo] = field(default_factory=list)
    similarity_score: float = 0.0

    @property
    def total_albums(self) -> int:
        return sum(a.album_count for a in self.artists)

    @property
    def total_tracks(self) -> int:
        return sum(a.track_count for a in self.artists)


class DuplicateFinder:
    """Finds duplicate artists in a music library"""

    def __init__(self, threshold: int = 80):
        self.threshold = threshold

    def normalize_name(self, name: str) -> str:
        name = name.strip()

        if ", " in name and name.count(",") == 1:
            parts = name.split(", ")
            if len(parts) == 2:
                if not any(word in parts[1].lower() for word in ["the", "and", "&", "feat", "with"]):
                    name = f"{parts[1]} {parts[0]}"

        name = name.lower()
        name = re.sub(r'\s+and\s+', ' & ', name)
        name = re.sub(r'\s+the\s+', ' ', name)
        name = re.sub(r'^the\s+', '', name)

        suffixes_to_remove = [
            r'\s*\(feat\..*?\)',
            r'\s*\(ft\..*?\)',
            r'\s*feat\..*$',
            r'\s*ft\..*$',
        ]
        for pattern in suffixes_to_remove:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        name = re.sub(r'\s+', ' ', name)
        name = name.strip()

        return name

    def suggest_canonical_name(self, names: list[str]) -> str:
        if not names:
            return ""

        if len(names) == 1:
            return names[0]

        scored_names = []
        for name in names:
            score = 0
            if ", " not in name:
                score += 10
            score += len(name) / 10
            if re.search(r'\s+(and|&)\s+the\s+', name, re.IGNORECASE):
                score += 5
            if name == name.title():
                score += 3
            if name != name.upper() and name != name.lower():
                score += 2
            scored_names.append((score, name))

        scored_names.sort(reverse=True)
        return scored_names[0][1]

    def find_duplicates(self, artists: list[ArtistInfo]) -> list[DuplicateGroup]:
        duplicate_groups = []
        processed_keys = set()

        normalized_map = defaultdict(list)
        for artist in artists:
            normalized = self.normalize_name(artist.title)
            normalized_map[normalized].append(artist)

        for normalized, group_artists in normalized_map.items():
            if len(group_artists) > 1:
                canonical = self.suggest_canonical_name([a.title for a in group_artists])
                group = DuplicateGroup(
                    canonical_name=canonical,
                    artists=group_artists,
                    similarity_score=100.0
                )
                duplicate_groups.append(group)
                for a in group_artists:
                    processed_keys.add(a.item_id)

        remaining = [a for a in artists if a.item_id not in processed_keys]
        remaining_normalized = [(self.normalize_name(a.title), a) for a in remaining]

        used_in_group = set()

        for i, (norm1, artist1) in enumerate(remaining_normalized):
            if artist1.item_id in used_in_group:
                continue

            current_group = [artist1]
            current_scores = []
            used_in_group.add(artist1.item_id)

            for j, (norm2, artist2) in enumerate(remaining_normalized[i+1:], i+1):
                if artist2.item_id in used_in_group:
                    continue

                similarity = fuzz.ratio(norm1, norm2)
                token_similarity = fuzz.token_sort_ratio(norm1, norm2)
                best_similarity = max(similarity, token_similarity)

                if best_similarity >= self.threshold:
                    current_group.append(artist2)
                    current_scores.append(best_similarity)
                    used_in_group.add(artist2.item_id)

            if len(current_group) > 1:
                canonical = self.suggest_canonical_name([a.title for a in current_group])
                avg_score = sum(current_scores) / len(current_scores) if current_scores else 100.0

                duplicate_groups.append(DuplicateGroup(
                    canonical_name=canonical,
                    artists=current_group,
                    similarity_score=avg_score
                ))

        duplicate_groups.sort(key=lambda g: g.total_tracks, reverse=True)

        return duplicate_groups

    def explain_match(self, name1: str, name2: str) -> str:
        norm1 = self.normalize_name(name1)
        norm2 = self.normalize_name(name2)

        explanations = []

        if norm1 == norm2:
            explanations.append("Names normalize to the same string")

            if ", " in name1 and ", " not in name2:
                explanations.append(f"'{name1}' appears to be in 'Last, First' format")
            elif ", " in name2 and ", " not in name1:
                explanations.append(f"'{name2}' appears to be in 'Last, First' format")

        else:
            similarity = fuzz.ratio(norm1, norm2)
            token_sim = fuzz.token_sort_ratio(norm1, norm2)

            if token_sim > similarity:
                explanations.append(f"Words match when reordered (score: {token_sim}%)")
            else:
                explanations.append(f"Names are similar (score: {similarity}%)")

        return "; ".join(explanations) if explanations else "Unknown match reason"
