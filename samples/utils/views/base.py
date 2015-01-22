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


"""General helper functions for the views.  Try to avoid using it outside the
views package.
"""

from __future__ import absolute_import, unicode_literals
import django.utils.six as six
from django.utils.six.moves import cStringIO as StringIO

import copy, re, csv
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.utils.translation import ugettext as _, ugettext_lazy
from django.contrib.contenttypes.models import ContentType
from django.template import defaultfilters
from jb_common import mimeparse
from samples import models, permissions
from samples.views.table_export import build_column_group_list, ColumnGroupsForm, \
    ColumnsForm, generate_table_rows, flatten_tree, OldDataForm, SwitchRowForm
import jb_common.utils.base


__all__ = ("sample_name_format", "get_renamable_name_formats", "verbose_sample_name_format", "get_sample",
           "does_sample_exist", "normalize_sample_name", "AmbiguityException", "lookup_sample", "convert_id_to_int",
           "successful_response", "remove_samples_from_my_samples", "StructuredSeries", "StructuredTopic",
           "build_structured_sample_list", "extract_preset_sample", "digest_process", "restricted_samples_query",
           "enforce_clearance", "UnicodeWriter", "table_export", "median", "average")


def sample_name_format(name, with_match_object=False):
    """Determines which sample name format the given name has.  It doesn't test
    whether the sample name is existing, nor if the initials are valid.

    :param name: the sample name

    :type name: unicode

    :return:
      The name of the sample name format and the respective match object.  The
      latter can be used to extract groups, for exampe.  ``None`` if the name
      had no valid format.

    :rtype: (unicode, re.MatchObject) or NoneType.
    """
    for name_format, properties in settings.SAMPLE_NAME_FORMATS.items():
        match = properties["regex"].match(name)
        if match:
            return (name_format, match) if with_match_object else name_format
    return (None, None) if with_match_object else None


renamable_name_formats = None
def get_renamable_name_formats():
    global renamable_name_formats
    if renamable_name_formats is None:
        renamable_name_formats = {name_format for (name_format, properties) in settings.SAMPLE_NAME_FORMATS.items()
                                  if properties.get("possible_renames")}
    return renamable_name_formats


def verbose_sample_name_format(name_format):
    """Returns the human-friendly, translatable name of the sample name format.  In
    English, it is in singular, and usable as an attribute to a noun.  In
    non-English language, you should choose something equivalent for the
    translation.

    :param name_format: The name format

    :type name_format: unicode

    :return:
      The verbose human-friendly name of this sample name format.

    :rtype: unicode
    """
    return settings.SAMPLE_NAME_FORMATS[name_format]["verbose_name"]


def get_sample(sample_name):
    """Lookup a sample by name.  You may also give an alias.  If more than one
    sample is found (can only happen via aliases), it returns a list.  Matching
    is exact.

    :param sample_name: the name or alias of the sample

    :type sample_name: unicode

    :return:
      the found sample.  If more than one sample was found, a list of them.  If
      none was found, ``None``.

    :rtype: `samples.models.Sample`, list of `samples.models.Sample`, or
      NoneType
    """
    try:
        sample = models.Sample.objects.get(name=sample_name)
    except models.Sample.DoesNotExist:
        aliases = [alias.sample for alias in models.SampleAlias.objects.filter(name=sample_name)]
        if len(aliases) == 1:
            return aliases[0]
        return aliases or None
    else:
        return sample


def does_sample_exist(sample_name):
    """Returns ``True`` if the sample name exists in the database.

    :param sample_name: the name or alias of the sample

    :type sample_name: unicode

    :return:
      whether a sample with this name exists

    :rtype: bool
    """
    return models.Sample.objects.filter(name=sample_name).exists() or \
        models.SampleAlias.objects.filter(name=sample_name).exists()


def normalize_sample_name(sample_name):
    """Returns the current name of the sample.

    :param sample_name: the name or alias of the sample

    :type sample_name: unicode

    :return:
      The current name of the sample.  This is only different from the input if
      you gave an alias.

    :rtype: unicode
    """
    if models.Sample.objects.filter(name=sample_name).exists():
        return sample_name
    try:
        sample_alias = models.SampleAlias.objects.get(name=sample_name)
    except models.SampleAlias.DoesNotExist:
        return
    else:
        return sample_alias.sample.name


class AmbiguityException(Exception):
    """Exception if a sample lookup leads to more than one matching alias
    (remember that alias names needn't be unique).  It is raised in
    `lookup_sample` and typically caught in JuliaBase's own middleware.
    """

    def __init__(self, sample_name, samples):
        self.sample_name, self.samples = sample_name, samples


def lookup_sample(sample_name, user, with_clearance=False):
    """Looks up the ``sample_name`` in the database (also among the aliases),
    and returns that sample if it was found *and* the current user is allowed
    to view it.  Shortened provisional names like “\*2” are also found.  If
    nothing is found or the permissions are not sufficient, it raises an
    exception.

    :param sample_name: name of the sample
    :param user: the currently logged-in user
    :param with_clearance: whether also clearances should be serached for and
        returned

    :type sample_name: unicode
    :type user: django.contrib.auth.models.User
    :type with_clearance: bool

    :return:
      the single found sample; or the sample and the clearance instance if this
      is necessary to view the sample and ``with_clearance=True``

    :rtype: `samples.models.Sample` or `samples.models.Sample`,
      `samples.models.Clearance`

    :raises Http404: if the sample name could not be found
    :raises AmbiguityException: if more than one matching alias was found
    :raises samples.permissions.PermissionError: if the user is not allowed to
      view the sample
    """
    name_format, match = sample_name_format(sample_name, with_match_object=True)
    if name_format == "provisional":
        sample_name = "*{0:05}".format(int(match.group("id")))
    sample = get_sample(sample_name)
    if not sample:
        raise Http404("Sample {name} could not be found (neither as an alias).".format(name=sample_name))
    if isinstance(sample, list):
        raise AmbiguityException(sample_name, sample)
    if with_clearance:
        clearance = permissions.get_sample_clearance(user, sample)
        return sample, clearance
    else:
        permissions.assert_can_fully_view_sample(user, sample)
        return sample


def convert_id_to_int(process_id):
    """If the user gives a process ID via the browser, it must be converted to
    an integer because this is what's stored in the database.  (Well, actually
    SQL gives a string, too, but that's beside the point.)  This routine
    converts it to a real integer and tests also for validity (not for
    availability in the database).

    :param process_id: the pristine process ID as given via the URL by the user

    :type process_id: str

    :return:
      the process ID as an integer number

    :rtype: int

    :raises Http404: if the process_id didn't represent an integer number.
    """
    # FixMe: This should be replaced with a function the gets the database
    # model class as an additional parameter and returns the found object,
    # along the lines of ``get_object_or_404``.  Then, it should also allow for
    # ``None`` as the `process_id`.
    try:
        return int(process_id)
    except ValueError:
        raise Http404("Invalid ID: “{id}”".format(id=process_id))


def successful_response(request, success_report=None, view=None, kwargs={}, query_string="", forced=False,
                        json_response=True):
    """After a POST request was successfully processed, there is typically a
    redirect to another page – maybe the main menu, or the page from where the
    add/edit request was started.

    The latter is appended to the URL as a query string with the ``next`` key,
    e.g.::

        /juliabase/6-chamber_deposition/08B410/edit/?next=/juliabase/samples/08B410a

    This routine generated the proper HttpResponse object that contains the
    redirection.  It always has HTTP status code 303 (“see other”).

    If the request came from the JuliaBase Remote Client, the response is a
    pickled ``json_response``.  (Normally, a simple ``True``.)

    :param request: the current HTTP request
    :param success_report: an optional short success message reported to the
        user on the next view
    :param view: the view name/function to redirect to; defaults to the main
        menu page (same when ``None`` is given)
    :param kwargs: group parameters in the URL pattern that have to be filled
    :param query_string: the *quoted* query string to be appended, without the
        leading ``"?"``
    :param forced: If ``True``, go to ``view`` even if a “next” URL is
        available.  Defaults to ``False``.  See `bulk_rename.bulk_rename` for
        using this option to generate some sort of nested forwarding.
    :param json_response: object which is to be sent as a pickled response to
        the remote client; defaults to ``True``.

    :type request: HttpRequest
    :type success_report: unicode
    :type view: str or function
    :type kwargs: dict
    :type query_string: unicode
    :type forced: bool
    :type json_response: ``object``

    :return:
      the HTTP response object to be returned to the view's caller

    :rtype: HttpResponse
    """
    if jb_common.utils.base.is_json_requested(request):
        return jb_common.utils.base.respond_in_json(json_response)
    return jb_common.utils.base.successful_response(request, success_report,
                                                    view or "samples.views.main.main_menu", kwargs,
                                                    query_string, forced)


def remove_samples_from_my_samples(samples, user):
    """Remove the given samples from the user's MySamples list

    :param samples: the samples to be removed.
    :param user: the user whose MySamples list is affected

    :type samples: list of `samples.models.Sample`
    :type user: django.contrib.auth.models.User
    """
    # FixMe: How does it react if a sample hasn't been in ``my_samples``?
    for sample in samples:
        sample.watchers.remove(user)


class StructuredSeries(object):
    """Helper class to pass sample series data to the main menu template.
    This is *not* a data strcuture for sample series.  It just stores all data
    needed to display a certain sample series to a certain user.  It is used in
    `StructuredTopic` and `build_structured_sample_list`.

    :ivar sample_series: the sample series for which data should be collected
      in this object
    :ivar name: the name of the sample series
    :ivar timestamp: the creation timestamp of the sample series
    :ivar samples: all samples belonging to this sample series, *and* being
      part the list of samples to be processed
    :ivar is_complete: a read-only property.  If ``False``, there are samples
      in the sample series not included into the list because they were missing
      on “My Samples”.  In other words, the user deliberately gets an
      incomplete list of samples and should be informed about it.

    :type sample_series: `samples.models.SampleSeries`
    :type name: unicode
    :type timestamp: datetime.datetime
    :type samples: list of `samples.models.Sample`
    :type is_complete: bool
    """

    def __init__(self, sample_series):
        self.sample_series = sample_series
        self.name = sample_series.name
        self.timestamp = sample_series.timestamp
        self.samples = []
        self.__is_complete = None

    def append(self, sample):
        """Adds a sample to this sample series view.

        :param sample: the sample

        :type sample: `samples.models.Sample`
        """
        assert self.__is_complete is None
        self.samples.append(sample)

    @property
    def is_complete(self):
        if self.__is_complete is None:
            sample_series_length = self.sample_series.samples.count()
            assert sample_series_length >= len(self.samples)
            self.__is_complete = sample_series_length == len(self.samples)
        return self.__is_complete


class StructuredTopic(object):
    """Class that represents one topic which contains samples and sample
    series, used for `build_structured_sample_list`.

    :ivar topic: the underlying JuliaBase topic which is represented by this
      instance.

    :ivar topic_name: the underlying JuliaBase topic's name which is
      represented by this instance.  It may be a surrogate name if the user is
      not allowed to see the actual name.

    :ivar samples: the samples of this topic which belong to the “My Samples”
      of the user but which don't belong to any sample series.

    :ivar sample_series: the sample series which belong to this topic and
      which contain “My Samples” of the user.  They themselves contain a list
      of their samples.  See `StructuredSeries` for further information.

    :type topic: `jb_common.models.Topic`
    :type samples: list of `samples.models.Sample`
    :type sample_series: list of `StructuredSeries`
    """

    def __init__(self, topic, user):
        self.topic = topic
        self.topic_name = topic.get_name_for_user(user)
        self.samples = []
        self.sample_series = []
        self.sub_topics = []

    def sort_sample_series(self):
        self.sample_series.sort(key=lambda series: series.timestamp, reverse=True)
        for topic in self.sub_topics:
            topic.sort_sample_series()

    def sort_sub_topics(self):
        if self.sub_topics:
            self.sub_topics = sorted(self.sub_topics,
                                     key=lambda structured_topic: structured_topic.topic.name)


def build_structured_sample_list(samples, user):
    """Generate a nested datastructure which contains the given samples in a
    handy way to be layouted in a certain way.  This routine is used for the
    “My Samples” list in the main menu, and for the multiple-selection box for
    samples in various views.  It is a list of `StructuredTopic` at the
    top-level.

    As far as sorting is concerned, all topics are sorted by alphabet, all
    sample series by reverse timestamp of origin, and all samples by name.

    :param samples: the samples to be processed; it doesn't matter if a sample
        occurs twice because this list is made unique first
    :param user: the user which sees the sample list eventually

    :type samples: list of `samples.models.Sample`
    :type user: django.contrib.auth.models.User

    :return:
      all topics of the user with his series and samples in them, all
      topicless samples; both is sorted

    :rtype: list of `StructuredTopic`, list of `samples.models.Sample`
    """
    def create_topic_tree(structured_topics):
        """Goes through all given topics and makes sure that all parent
        topics are also included.
        """
        for structured_topic in structured_topics.values():
            if structured_topic.topic.has_parent():
                try:
                    parent_structured_topic = structured_topics[structured_topic.topic.parent_topic.id]
                except KeyError:
                    structured_topics[structured_topic.topic.parent_topic.id] = \
                        StructuredTopic(structured_topic.topic.parent_topic, user)
                    # i must recall the function because the size of the dictionary has changed
                    return create_topic_tree(structured_topics)
                parent_structured_topic.sub_topics.append(structured_topic)
                parent_structured_topic.sort_sub_topics()
                sub_topics.append(structured_topic.topic.id)
            structured_topic.sort_sample_series()
        return structured_topics

    structured_series = {}
    structured_topics = {}
    topicless_samples = []
    sub_topics = []
    for sample in sorted(set(samples), key=lambda sample: (sample.tags, sample.name)):
        containing_series = sample.series.all()
        if containing_series:
            for series in containing_series:
                if series.name not in structured_series:
                    structured_series[series.name] = StructuredSeries(series)
                    topic_id = series.topic.id
                    if topic_id not in structured_topics:
                        structured_topics[topic_id] = StructuredTopic(series.topic, user)
                    structured_topics[topic_id].sample_series.append(structured_series[series.name])
                structured_series[series.name].append(sample)
        elif sample.topic:
            topic_id = sample.topic.id
            if topic_id not in structured_topics:
                structured_topics[topic_id] = StructuredTopic(sample.topic, user)
            structured_topics[topic_id].samples.append(sample)
        else:
            topicless_samples.append(sample)
    structured_topics = create_topic_tree(structured_topics)
    for topic_id in sub_topics:
        # FixMe: This try block hides a bug in the above code.  Remove both.
        try:
            del structured_topics[topic_id]
        except KeyError:
            continue
    structured_topics = sorted(structured_topics.values(),
                               key=lambda structured_topic: structured_topic.topic.name)
    return structured_topics, topicless_samples


def extract_preset_sample(request):
    """Extract a sample from a query string.  All physical processes as well
    as result processes may have an optional parameter in the query string,
    namely the sample to which they should be applied (results even a sample
    series, too).  If such a parameter is present, the given sample – if
    existing – must be added to the list of selectable samples, and it must be
    the initially marked sample.

    This routine is used in all views for creating physical processes.  It is
    not used for result processes because they need a given sample *series*,
    too, and this would have been over-generalisation.

    This routine extracts the sample name from the query string and returns the
    sample.  If nothing was given or the sample non-existing, it returns
    ``None``.

    :param request: the current HTTP Request object

    :type request: HttpRequest

    :return:
      the sample given in the query string, if any

    :rtype: `samples.models.Sample` or NoneType
    """
    if "sample" in request.GET:
        try:
            return models.Sample.objects.get(name=request.GET["sample"])
        except models.Sample.DoesNotExist:
            pass


def digest_process(process, user, local_context={}):
    """Convert a process to a process context.  This conversion extracts the
    relevant information of the process and saves it in a form which can easily
    be processed in a template.

    :param process: the process to be digest
    :param user: current user
    :param local_context: the local sample context; for example, this is
      relevant to ``SampleSplit``, see
      :py:meth:`samples.models.SampleSplit.get_cache_key`. 

    :type process: `samples.models.Process`
    :type user: django.contrib.auth.models.User
    :type local_context: dict mapping str to ``object``

    :return:
      the process context of the given process

    :rtype: dict mapping str to ``object``
    """
    process = process.actual_instance
    cache_key = process.get_cache_key(user.jb_user_details.get_data_hash(), local_context)
    cached_context = jb_common.utils.base.get_from_cache(cache_key) if cache_key else None
    if cached_context is None:
        process_context = process.get_context_for_user(user, local_context)
        if cache_key:
            keys_list_key = "process-keys:{0}".format(process.id)
            with jb_common.utils.base.cache_key_locked("process-lock:{0}".format(process.id)):
                keys = cache.get(keys_list_key, [])
                keys.append(cache_key)
                cache.set(keys_list_key, keys, settings.CACHES["default"].get("TIMEOUT", 300) + 10)
                cache.set(cache_key, process_context)
    else:
        cached_context.update(local_context)
        process_context = process.get_context_for_user(user, cached_context)
    return process_context


def restricted_samples_query(user):
    """Returns a QuerySet which is restricted to samples the names of which the
    given user is allowed to see.  Note that this doesn't mean that the user is
    allowed to see all of the samples themselves necessarily.  It is only about
    the names.  See the :py:func:`samples.views.sample.search` view for further
    information.

    :param user: the user for which the allowed samples should be retrieved

    :type user: django.contrib.auth.models.User

    :return:
      a queryset with all samples the names of which the user is allowed to
      know

    :rtype: QuerySet
    """
    if user.is_staff:
        return models.Sample.objects.all().order_by("name")
    return models.Sample.objects.filter(Q(topic__confidential=False) | Q(topic__members=user) |
                                        Q(currently_responsible_person=user) | Q(clearances__user=user) |
                                        Q(topic__isnull=True)).order_by("name").distinct()


def enforce_clearance(user, clearance_processes, destination_user, sample, clearance=None, cutoff_timestamp=None):
    """Unblocks specified processes of a sample for a given user.

    :param user: the user who unblocks the processes
    :param clearance_processes: all process classes that the destination user
        should be able to see; ``"all"`` means all processes
    :param destination_user: the user for whom the sample should be unblocked
    :param sample: the sample to be unblocked
    :param clearance: The current clearance to which further unblocked processes
        should be added.  This is only used in the internal recursion of this
        routine in order to traverse through sample splits upwards.
    :param cutoff_timestamp: The timestamp after which no processes in the
        sample should be unblocked.  This is only used in the internal
        recursion of this routine in order to traverse through sample splits
        upwards.  It is a similar algorithm as the one used in
        `samples.views.sample.SamplesAndProcesses`.

    :type user: django.contrib.auth.models.User
    :type clearance_processes: tuple of `samples.models.Process`, or str
    :type destination_user: django.contrib.auth.models.User
    :type sample: `samples.models.Sample`
    :type clearance: `samples.models.Clearance`
    :type cutoff_timestamp: datetime.datetime
    """
    if not clearance:
        clearance, __ = models.Clearance.objects.get_or_create(user=destination_user, sample=sample)
    base_query = sample.processes.filter(finished=True)
    processes = base_query if not cutoff_timestamp else base_query.filter(timestamp__lte=cutoff_timestamp)
    for process in processes:
        process = process.actual_instance
        if isinstance(process, models.Result) and permissions.has_permission_to_view_result_process(user, process):
            clearance.processes.add(process)
        elif isinstance(process, models.PhysicalProcess) and \
                permissions.has_permission_to_view_physical_process(user, process):
            if clearance_processes == "all" or isinstance(process, clearance_processes):
                clearance.processes.add(process)
    split_origin = sample.split_origin
    if split_origin:
        enforce_clearance(user, clearance_processes, destination_user, split_origin.parent, clearance,
                          split_origin.timestamp)


class UnicodeWriter(object):
    """Convert a two-dimensional data structure into a UTF-8-encoded CSV byte
    string.  Inspired by <http://docs.python.org/library/csv.html#examples>.
    """
    # FixMe: This must go when dropping support for Python 2.

    def __init__(self, stream=None, dialect=csv.excel_tab, encoding="utf-8", **kwargs):
        """Additional keyword arguments are passed to the
        ``csv.writer`` factory function in Python's ``csv`` module.  After
        having instantiated this class, you can use `writerow` and `writerows`
        to add data to it, and then extract it in the CSV format using
        `getvalue`.

        :param stream: the writable file-like object where the output should be
            sent; if ``None``, you must get the outout with `getvalue`.
        :param dialect: the CSV format; it defaults to Excel's TAB format
            (TAB-separated, double-quotes)
        :param encoding: name of the output encoding to be used; defaults to
            UTF-8

        :type stream: file
        :type dialect: ``csv.Dialect``
        :type encoding: str
        """
        import codecs
        self.queue = StringIO()
        self.writer = csv.writer(self.queue, dialect=dialect, **kwargs)
        self.stream = stream if stream else StringIO()
        self.encoder = codecs.getincrementalencoder(encoding)()

    def writerow(self, row):
        """Add the given row to the output.

        :param row: list of the table cells

        :type row: list of object
        """
        output_row = []
        for s in row:
            if s is None:
                output_row.append("")
            else:
                output_row.append(six.text_type(s).encode("utf-8"))
        self.writer.writerow(output_row)
        data = self.queue.getvalue()
        data = data.decode("utf-8")
        data = self.encoder.encode(data)
        self.stream.write(data)
        self.queue.truncate(0)

    def writerows(self, rows):
        """Add the given rows to the output.

        :param rows: list of rows; each row is a list of table cells

        :type rows: list of list of object
        """
        for row in rows:
            self.writerow(row)

    def getvalue(self):
        """Get the output so far.  Normally, you will call this method after
        the instance was filled with all data.  Thus, after called this method,
        the instance of ``UnicodeWriter`` is no longer used.

        :return:
          the table in CSV format, as an encoded octet string

        :rtype: str
        """
        return self.stream.getvalue()


def table_export(request, data, label_column_heading):
    """Helper function which does almost all work needed for a CSV table
    export view.  This is not a view per se, however, it is called by views,
    which have to do almost nothing anymore by themselves.  See for example
    `sample.export`.

    This function return the data in JSON format if this is requested by the
    ``Accept`` header field in the HTTP request.

    :param request: the current HTTP Request object
    :param data: the root node of the data tree
    :param label_column_heading: Description of the very first column with the
        table row headings, see `generate_table_rows`.

    :type request: HttpRequest
    :type data: `samples.data_tree.DataNode`
    :type label_column_heading: unicode

    :return:
      the HTTP response object or a tuple with all needed forms to create the export view

    :rtype: HttpResponse or tuple of django.forms.Form
    """
    if not data.children:
        # We have no rows (e.g. a result process with only one row), so let's
        # turn the root into the only row.
        root_without_children = copy.copy(data)
        # Remove the label column (the zeroth column)
        root_without_children.descriptive_name = None
        data.children = [root_without_children]
    get_data = request.GET if any(key.startswith("__old_data") for key in request.GET) else None
    requested_mime_type = mimeparse.best_match(["text/csv", "application/json"], request.META.get("HTTP_ACCEPT", "text/csv"))
    data.find_unambiguous_names()
    data.complete_items_in_children()
    column_groups, columns = build_column_group_list(data)
    single_column_group = {column_groups[0].name} if len(column_groups) == 1 else set()
    table = switch_row_forms = None
    selected_column_groups = single_column_group
    selected_columns = set()
    column_groups_form = ColumnGroupsForm(column_groups, get_data) if not single_column_group else None
    previous_data_form = OldDataForm(get_data)
    if previous_data_form.is_valid():
        previous_column_groups = previous_data_form.cleaned_data["column_groups"]
        previous_columns = previous_data_form.cleaned_data["columns"]
    else:
        previous_column_groups = previous_columns = frozenset()
    columns_form = ColumnsForm(column_groups, columns, previous_column_groups, get_data)
    if single_column_group or column_groups_form.is_valid():
        selected_column_groups = single_column_group or column_groups_form.cleaned_data["column_groups"]
        if columns_form.is_valid():
            selected_columns = columns_form.cleaned_data["columns"]
            label_column = [row.descriptive_name for row in data.children]
            table = generate_table_rows(flatten_tree(data), columns, columns_form.cleaned_data["columns"],
                                        label_column, label_column_heading)
            start_column_index = 1 if any(label_column) else 0
            if not(previous_columns) and selected_columns:
                switch_row_forms = [SwitchRowForm(prefix=str(i), initial={"active": any(row[start_column_index:])})
                                    for i, row in enumerate(table)]
            else:
                switch_row_forms = [SwitchRowForm(get_data, prefix=str(i)) for i in range(len(table))]
            all_switch_row_forms_valid = all([switch_row_form.is_valid() for switch_row_form in switch_row_forms])
            if all_switch_row_forms_valid and \
                    previous_column_groups == selected_column_groups and previous_columns == selected_columns:
                reduced_table = [row for i, row in enumerate(table) if switch_row_forms[i].cleaned_data["active"] or i == 0]
                if requested_mime_type == "application/json":
                    data = [dict((reduced_table[0][i], cell) for i, cell in enumerate(row) if cell)
                            for row in reduced_table[1:]]
                    return jb_common.utils.base.respond_in_json(data)
                else:
                    response = HttpResponse(content_type="text/csv; charset=utf-8")
                    response['Content-Disposition'] = \
                        "attachment; filename=juliabase--{0}.txt".format(defaultfilters.slugify(data.descriptive_name))
                    writer = UnicodeWriter(response) if six.PY2 else csv.writer(response, dialect=csv.excel_tab)
                    writer.writerows(reduced_table)
                return response
    if selected_column_groups != previous_column_groups:
        columns_form = ColumnsForm(column_groups, columns, selected_column_groups, initial={"columns": selected_columns})
    old_data_form = OldDataForm(initial={"column_groups": selected_column_groups, "columns": selected_columns})
    return (column_groups_form, columns_form, table, switch_row_forms, old_data_form)


def median(numeric_values):
    """Calculates the median from a list of numeric values.

    :param numeric_values: a list with numeric values

    :type numeric_values: list

    :retrun:
      The median of the given values

    :rtype: int or float
    """
    if isinstance(numeric_values, (tuple, list)) and len(numeric_values) > 0:
        values = sorted(numeric_values)
        if len(values) % 2 == 1:
            return values[(len(values) + 1) / 2 - 1]
        else:
            lower = values[len(values) / 2 - 1]
            upper = values[len(values) / 2]
            return (float(lower + upper)) / 2


def average(numeric_values):
    """Calculates the average value from a list of numeric values.

    :param numeric_values: a list with numeric values

    :type numeric_values: list

    :retrun:
      The average value of the given values

    :rtype: float
    """
    if isinstance(numeric_values, (tuple, list)) and len(numeric_values) > 0:
        return sum(map(float, numeric_values)) / len(numeric_values)
