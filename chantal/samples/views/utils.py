#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
from django.newforms.util import ErrorList, ValidationError
from django.http import HttpResponseRedirect, QueryDict
from django.utils.translation import ugettext_lazy as _
from functools import update_wrapper
from chantal.samples import models
from django.newforms import ModelForm

class DataModelForm(ModelForm):
    def uncleaned_data(self, fieldname):
        return self.data.get(self.prefix + "-" + fieldname)

time_pattern = re.compile(r"^\s*((?P<H>\d{1,3}):)?(?P<M>\d{1,2}):(?P<S>\d{1,2})\s*$")
def clean_time_field(value):
    if not value:
        return ""
    match = time_pattern.match(value)
    if not match:
        raise ValidationError(_("Time must be given in the form HH:MM:SS."))
    hours, minutes, seconds = match.group("H"), int(match.group("M")), int(match.group("S"))
    hours = int(hours) if hours is not None else 0
    if minutes >= 60 or seconds >= 60:
        raise ValidationError(_("Minutes and seconds must be smaller than 60."))
    if not hours:
        return "%d:%02d" % (minutes, seconds)
    else:
        return "%d:%02d:%02d" % (hours, minutes, seconds)

quantity_pattern = re.compile(ur"^\s*(?P<number>[-+]?\d+(\.\d+)?(e[-+]?\d+)?)\s*(?P<unit>[a-uA-Zµ]+)\s*$")
def clean_quantity_field(value, units):
    if not value:
        return ""
    value = unicode(value).replace(",", ".").replace(u"μ", u"µ")
    match = quantity_pattern.match(value)
    if not match:
        raise ValidationError(_("Must be a physical quantity with number and unit."))
    original_unit = match.group("unit").lower()
    for unit in units:
        if unit.lower() == original_unit.lower():
            break
    else:
        raise ValidationError(_("The unit is invalid.  Valid units are: %s")%", ".join(units))
    return match.group("number") + " " + unit
    
def int_or_zero(number):
    try:
        return int(number)
    except ValueError:
        return 0

def append_error(form, fieldname, error_message):
    form._errors.setdefault(fieldname, ErrorList()).append(error_message)

class _PermissionCheck(object):
    def __init__(self, original_view_function, permission):
        self.original_view_function, self.permission = original_view_function, "samples."+permission
        update_wrapper(self, original_view_function)
    def __call__(self, request, *args, **kwargs):
        if self.permission not in request.user.get_all_permissions():
            return HttpResponseRedirect("permission_error")
        return self.original_view_function(request, *args, **kwargs)
    
def check_permission(permission):
    def decorate(original_view_function):
        return _PermissionCheck(original_view_function, permission)
    return decorate

def get_sample(sample_name):
    try:
        sample = models.Sample.objects.get(name=sample_name)
    except models.Sample.DoesNotExist:
        try:
            sample_alias = models.SampleAlias.objects.get(name=sample_name)
        except models.SampleAlias.DoesNotExist:
            return
        else:
            return sample_alias.sample
    else:
        return sample

def does_sample_exist(sample_name):
    return bool(models.Sample.objects.filter(name=sample_name).count() or
                models.SampleAlias.objects.filter(name=sample_name).count())

def normalize_sample_name(sample_name):
    if models.Sample.objects.filter(name=sample_name).count():
        return sample_name
    try:
        sample_alias = models.SampleAlias.objects.get(name=sample_name)
    except models.SampleAlias.DoesNotExist:
        return
    else:
        return sample_alias.sample.name

level0_pattern = re.compile(ur"(?P<level0_index>\d+)-(?P<id>.+)")
level1_pattern = re.compile(ur"(?P<level0_index>\d+)_(?P<level1_index>\d+)-(?P<id>.+)")
def normalize_prefixes(post_data):
    level0_indices = set()
    level1_indices = {}
    digested_post_data = {}
    for key in post_data:
        match = level0_pattern.match(key)
        if match:
            level0_index = int(match.group("level0_index"))
            level0_indices.add(level0_index)
            level1_indices.setdefault(level0_index, set())
            digested_post_data[(level0_index, match.group("id"))] = post_data.getlist(key)
        else:
            match = level1_pattern.match(key)
            if match:
                level0_index, level1_index = int(match.group("level0_index")), int(match.group("level1_index"))
                level1_indices.setdefault(level0_index, set()).add(level1_index)
                digested_post_data[(level1_index, level0_index, match.group("id"))] = post_data.getlist(key)
            else:
                digested_post_data[key] = post_data.getlist(key)
    level0_indices = sorted(level0_indices)
    normalization_necessary = level0_indices and level0_indices[-1] != len(level0_indices) - 1
    for key, value in level1_indices.iteritems():
        level1_indices[key] = sorted(value)
        normalization_necessary = normalization_necessary or (
            level1_indices[key] and level1_indices[key][-1] != len(level1_indices[key]) - 1)
    if normalization_necessary:
        new_post_data = QueryDict("").copy()
        for key, value in digested_post_data.iteritems():
            if isinstance(key, basestring):
                new_post_data.setlist(key, value)
            elif len(key) == 2:
                new_post_data.setlist("%d-%s" % (level0_indices.index(key[0]), key[1]), value)
            else:
                new_level0_index = level0_indices.index(key[1])
                new_post_data.setlist("%d_%d-%s" % (new_level0_index, level1_indices[key[1]].index(key[0]), key[2]), value)
    else:
        new_post_data = post_data
    return new_post_data, len(level0_indices), [len(level1_indices[i]) for i in level0_indices]
