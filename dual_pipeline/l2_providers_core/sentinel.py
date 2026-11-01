import threading

_l2d_authorization_lock = threading.Lock()
_l2d_authorized = False

def authorize_provider_instantiation() -> None:
    global _l2d_authorized
    with _l2d_authorization_lock:
        _l2d_authorized = True

def require_l2d_authorization() -> None:
    global _l2d_authorized
    with _l2d_authorization_lock:
        if not _l2d_authorized:
            from dual_pipeline.exceptions import ProviderAccessViolation
            raise ProviderAccessViolation(
                "Lazy Provider Initialization Sentinel: Provider instantiated before L2D explicitly authorized it."
            )

def reset_l2d_authorization() -> None:
    global _l2d_authorized
    with _l2d_authorization_lock:
        _l2d_authorized = False
