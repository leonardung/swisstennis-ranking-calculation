"""Swiss Tennis ranking algorithm (DCL 2025, Art. 5).

For a player with start value w0, wins against opponents w_i and (counted) losses against w_j:

    P = ln(e^w0  + sum_i e^w_i)
    N = ln(e^-w0 + sum_j e^-w_j)
    W = (P - N) / 2        competition value
    R = (P + N) / 6        risk bonus ("frequent player bonus")
    C = W + R              ranking value

W is computed in 5 passes; in each pass opponents are valued with their W from the previous
pass (pass 1 uses their start value w0). R is computed with the opponent values of the last
pass. Per 6 matches played, one loss (at most 4) against the weakest opponent is not counted.
With no matches, W = w0 and R = 0.
"""

import numpy as np

PASSES = 5
MATCHES_PER_DISCARDED_LOSS = 6
MAX_DISCARDED_LOSSES = 4


def counted_losses(player: np.ndarray, opp_w: np.ndarray, win: np.ndarray, n_players: int) -> np.ndarray:
    """Mask of losses that count, after discarding floor(matches/6) (max 4) weakest-opponent losses."""
    n_matches = np.bincount(player, minlength=n_players)
    n_discard = np.minimum(n_matches // MATCHES_PER_DISCARDED_LOSS, MAX_DISCARDED_LOSSES)

    loss_rows = np.flatnonzero(~win)
    order = loss_rows[np.lexsort((opp_w[loss_rows], player[loss_rows]))]
    sorted_players = player[order]
    rank_in_player = np.arange(len(order)) - np.searchsorted(sorted_players, sorted_players)

    counted = np.zeros(len(player), dtype=bool)
    counted[order] = rank_in_player >= n_discard[sorted_players]
    return counted


def single_pass(
    w0: np.ndarray, player: np.ndarray, opp_w: np.ndarray, win: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """One pass of the formula given fixed opponent values. Returns (W, R) per player."""
    n = len(w0)
    loss = counted_losses(player, opp_w, win, n)
    pos = np.log(np.exp(w0) + np.bincount(player, weights=np.exp(opp_w) * win, minlength=n))
    neg = np.log(np.exp(-w0) + np.bincount(player, weights=np.exp(-opp_w) * loss, minlength=n))
    return (pos - neg) / 2, (pos + neg) / 6


def compute(
    w0: np.ndarray,
    player: np.ndarray,
    opp: np.ndarray,
    opp_fixed: np.ndarray,
    win: np.ndarray,
    passes: int = PASSES,
    fixed: np.ndarray | None = None,
    with_opp_w: bool = False,
):
    """Run the iterative calculation for all players at once.

    w0:        start value per player (index = player number)
    player:    player number of each match row (each match seen from that player's side)
    opp:       opponent's player number, or -1 when the opponent is not part of the calculation
    opp_fixed: value used for opponents with opp == -1 (the adversary value stored with the result)
    win:       True if the player won that match
    fixed:     optional value per player (NaN = computed) that opponents use in every pass
               instead of the player's current W (players with an assigned value)

    Returns (W, R), or (W, R, opp_w) with the opponent value of each match row in the last pass
    when with_opp_w is set.
    """
    known = opp >= 0
    W = w0.copy()
    for _ in range(passes):
        V = W if fixed is None else np.where(np.isnan(fixed), W, fixed)
        opp_w = np.where(known, V[np.where(known, opp, 0)], opp_fixed)
        W, R = single_pass(w0, player, opp_w, win)
    return (W, R, opp_w) if with_opp_w else (W, R)

