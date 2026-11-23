import type { CounterfactualBranchState } from '../types/sessionEvents';

export function branchTitle(branch: CounterfactualBranchState): string {
  return branch.source === 'SYNTHETIC_FIXTURE'
    ? 'SYNTHETIC · BRANCH/SIMULATION'
    : 'BRANCH/SIMULATION';
}

export function branchVerdict(branch: CounterfactualBranchState): string | null {
  if (branch.adopted === undefined) return null;
  return branch.adopted
    ? `adopted (${branch.adoptionMethod ?? 'method unavailable'})`
    : `discarded (${branch.adoptionMethod ?? 'not adopted'})`;
}

export function branchEmptyState(branches: CounterfactualBranchState[]): string | null {
  return branches.length === 0 ? 'No counterfactual activity' : null;
}
