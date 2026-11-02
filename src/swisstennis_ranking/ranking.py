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

from typing import NamedTuple

import numpy as np

PASSES = 5
MATCHES_PER_DISCARDED_LOSS = 6
MAX_DISCARDED_LOSSES = 4


class LossGroups(NamedTuple):
    """The losses that can be discarded, grouped by player: those of players with at least
    MATCHES_PER_DISCARDED_LOSS matches. They depend only on who played and lost, so one grouping
    serves every pass."""

    rows: np.ndarray  # match rows, grouped by player, in row order within a player
    starts: np.ndarray  # first position of each player's group in `rows`
    group: np.ndarray  # group number of each position
    n_discard: np.ndarray  # losses to discard, per group


def loss_groups(player: np.ndarray, win: np.ndarray, n_players: int) -> LossGroups:
    n_discard = np.minimum(np.bincount(player, minlength=n_players) // MATCHES_PER_DISCARDED_LOSS, MAX_DISCARDED_LOSSES)
    rows = np.flatnonzero(~win & (n_discard[player] > 0))
    rows = rows[np.argsort(player[rows], kind="stable")]
    starts = np.flatnonzero(np.r_[True, player[rows][1:] != player[rows][:-1]]) if len(rows) else rows
    group = np.repeat(np.arange(len(starts)), np.diff(np.r_[starts, len(rows)]))
    return LossGroups(rows, starts, group, n_discard[player[rows[starts]]])


def counted_losses(
    player: np.ndarray, opp_w: np.ndarray, win: np.ndarray, n_players: int, groups: LossGroups | None = None
) -> np.ndarray:
    """Mask of losses that count, after discarding floor(matches/6) (max 4) weakest-opponent losses
    (ties: the earlier row). groups: loss_groups() of these matches, when already known."""
    g = loss_groups(player, win, n_players) if groups is None else groups
    counted = ~win
    vals = opp_w[g.rows].astype(float)  # a copy; a discarded loss becomes NaN, which fmin skips
    pos = np.arange(len(g.rows))
    # the weakest remaining loss of each player, as often as the player discards (at most 4 rounds)
    for k in range(1, int(g.n_discard.max(initial=0)) + 1):
        low = np.fmin.reduceat(vals, g.starts)
        first = np.minimum.reduceat(np.where(vals == low[g.group], pos, len(pos)), g.starts)
        take = first[(g.n_discard >= k) & (first < len(pos))]
        counted[g.rows[take]] = False
        vals[take] = np.nan
    return counted


def single_pass(
    w0: np.ndarray, player: np.ndarray, opp_w: np.ndarray, win: np.ndarray, counted: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """One pass of the formula given fixed opponent values. Returns (W, R) per player.
    counted: which losses count (default: all but the discarded ones, see counted_losses)."""
    n = len(w0)
    loss = counted_losses(player, opp_w, win, n) if counted is None else counted & ~win
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
    with_rows: bool = False,
):
    """Run the iterative calculation for all players at once.

    w0:        start value per player (index = player number)
    player:    player number of each match row (each match seen from that player's side)
    opp:       opponent's player number, or -1 when the opponent is not part of the calculation
    opp_fixed: value used for opponents with opp == -1 (the adversary value stored with the result)
    win:       True if the player won that match
    fixed:     optional value per player (NaN = computed) that opponents use in every pass
               instead of the player's current W (players with an assigned value)

    Returns (W, R), or with with_rows (W, R, opp_w, counted): per match row, the opponent value
    of the last pass and whether the loss counted in it (see counted_losses; False for wins).
    """
    known = opp >= 0
    groups = loss_groups(player, win, len(w0))
    W = w0.copy()
    for _ in range(passes):
        V = W if fixed is None else np.where(np.isnan(fixed), W, fixed)
        opp_w = np.where(known, V[np.where(known, opp, 0)], opp_fixed)
        counted = counted_losses(player, opp_w, win, len(w0), groups)
        W, R = single_pass(w0, player, opp_w, win, counted)
    return (W, R, opp_w, counted) if with_rows else (W, R)

