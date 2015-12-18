#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    dropback.__main__
    ~~~~~~~~~~~~~~

    Wraps backup.py

    :author: Jonathan Love
    :copyright: (c) 2015 by Doubledot Media Ltd
    :license: See README.md and LICENSE for more details
    :version: 0.1.alpha1
"""

import logging

if __name__ == '__main__':
    from .backup import main
    logging.basicConfig(level=logging.INFO)
    main()