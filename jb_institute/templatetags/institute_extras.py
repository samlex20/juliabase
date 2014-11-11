#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# This file is part of JuliaBase, the samples database.
#
# Copyright © 2008–2014 Forschungszentrum Jülich, Germany,
#                       Marvin Goblet <m.goblet@fz-juelich.de>,
#                       Torsten Bronger <t.bronger@fz-juelich.de>
#
# You must not use, install, pass on, offer, sell, analyse, modify, or
# distribute this software without explicit permission of the copyright holder.
# If you have received a copy of this software without the explicit permission
# of the copyright holder, you must destroy it immediately and completely.


"""Collection of tags and filters that are used in the institute templates.
"""

from __future__ import division, absolute_import, unicode_literals

import os.path
from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext as _
from jb_institute.views import form_utils
from jb_institute import models
from django.template.defaultfilters import stringfilter

register = template.Library()


@register.filter
def three_digits(number):
    """Filter for padding an integer with zeros so that it has at least three
    digits.
    """
    return mark_safe(form_utils.three_digits(number))


@register.filter
def calculate_silane_concentration(value):
    """Filter for calculating the silane concentration for a
    deposition layer from the silane and hydrogen fluxes.
    """
    if value.sih4 == None or value.h2 == None:
        return None
    silane = float(value.sih4) * 0.6
    hydrogen = float(value.h2)
    if silane + hydrogen == 0:
        return None
    # Cheap way to cut the digits
    calculate_sc = lambda s, h: float("{0:5.2f}".format(100 * s / (s + h))) if s + h != 0 else "NaN"
    sc = calculate_sc(silane, hydrogen)
    try:
        silane_end = float(value.sih4_end) * 0.6 if value.sih4_end is not None else silane
        hydrogen_end = float(value.h2_end) if value.h2_end is not None else hydrogen
        sc_end = calculate_sc(silane_end, hydrogen_end)
    except AttributeError:
        return sc
    if value.sih4_end is None and value.h2_end is None:
        return sc
    else:
        return sc, sc_end


@register.filter
def cluster_tool_layer_type(value, type_="standard"):
    """Filter for detecting the layer type (hot-wire, PECVD) for
    cluster-tool layers.  ``type_`` may be ``"standard"``, ``"short"``, or
    ``"verbose"``."""
    if isinstance(value, models.ClusterToolHotWireLayer):
        return {"verbose": _("hot-wire"), "short": "HW"}.get(type_, "hot-wire")
    elif isinstance(value, models.ClusterToolPECVDLayer):
        return {"verbose": "PECVD", "short": "P"}.get(type_, "PECVD")


@register.filter
@stringfilter
def basename(filepath):
    return os.path.basename(filepath)


@register.filter
def solarsimulator_color(measurement):
    """Returns the colour which is associated with the solarsimulator
    measurement.  The returned string is ready-to-be-used in CSS directives as
    a colour name.
    """
    return {"dark": "gray", "AM1.5": "inherited", "BG7": "lightblue", "OG590": "darkorange"}[measurement.irradiance]


@register.filter
def sort_cells(cells):
    """Sort solarsimulator cells smartly.
    """
    def sort_function(cell):
        try:
            return int(cell.position)
        except ValueError:
            return cell.position
    return sorted(cells, key=sort_function)


@register.filter
def depostion_time(time):
    if time:
        time_components = time.split(":")
        if len(time_components) == 2:
            minutes, seconds = time_components
            hours = 0
        else:
            hours, minutes, seconds = time_components
        return "{0}:{1:02}:{2:02}".format(int(hours), int(minutes), int(seconds))
    else:
        return "—"

@register.filter
def short_filepath(filepath):
    return filepath[filepath.rfind("/") + 1:]
