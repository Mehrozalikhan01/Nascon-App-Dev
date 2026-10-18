"""QR route selection and inventory-safe guest cart merge helpers."""


def match_target_route(entity_id: str, state: str) -> str:
    if state == "upcoming":
        return f"/web/matches/{entity_id}/preview"
    if state == "in_progress":
        return f"/web/matches/{entity_id}/live"
    if state == "completed":
        return f"/web/matches/{entity_id}/vod"
    return f"/web/matches/{entity_id}/notice?state={state}"


def target_route(entity_type: str, entity_id: str, state: str) -> str:
    if entity_type == "match":
        return match_target_route(entity_id, state)
    if entity_type == "costream":
        return f"/web/costream/{entity_id}"
    if entity_type == "player":
        return f"/web/players/{entity_id}"
    return f"/web/clubs/{entity_id}"


def merge_guest_items(existing: list[dict], guest_items: list[dict], stock_lookup) -> tuple[list[dict] | None, str | None]:
    merged = {item["variant_id"]: dict(item) for item in existing}
    for requested in guest_items:
        variant_id = requested["variant_id"]
        inventory = stock_lookup(variant_id)
        quantity = requested["quantity"] + merged.get(variant_id, {}).get("quantity", 0)
        if inventory is None:
            return None, "VARIANT_NOT_FOUND"
        if quantity > inventory["stock"]:
            return None, variant_id
        merged[variant_id] = {
            "variant_id": variant_id,
            "quantity": quantity,
            "unit_price_minor": int(inventory.get("price_minor", 450000)),
        }
    return list(merged.values()), None
