"""Prepare the original system program image before the bounded n2m_system simulation.

Spec: wiki/src/nand2mario/scripts/regression/spec.md.
"""
import sys

if __package__:
    from . import cpu_regression
else:
    import cpu_regression


def main(argv=None):
    return cpu_regression.main(argv, program='system')


if __name__ == '__main__':
    raise SystemExit(main())
