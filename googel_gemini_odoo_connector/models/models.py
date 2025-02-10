from odoo.models import BaseModel
from odoo.api import Self, ValuesType, IdType
from collections import defaultdict
from odoo import SUPERUSER_ID,_
from odoo.exceptions import ValidationError,AccessError,UserError
import typing
from odoo.models import LOG_ACCESS_COLUMNS,_unlink
from odoo.tools import SQL
import json

def create_new(self, vals_list: list[ValuesType]) -> Self:
    """ create(vals_list) -> records

    Creates new records for the model.

    The new records are initialized using the values from the list of dicts
    ``vals_list``, and if necessary those from :meth:`~.default_get`.

    :param Union[list[dict], dict] vals_list:
        values for the model's fields, as a list of dictionaries::

            [{'field_name': field_value, ...}, ...]

        For backward compatibility, ``vals_list`` may be a dictionary.
        It is treated as a singleton list ``[vals]``, and a single record
        is returned.

        see :meth:`~.write` for details

    :return: the created records
    :raise AccessError: if the current user is not allowed to create records of the specified model
    :raise ValidationError: if user tries to enter invalid value for a selection field
    :raise ValueError: if a field name specified in the create values does not exist.
    :raise UserError: if a loop would be created in a hierarchy of objects a result of the operation
      (such as setting an object as its own parent)
    """
    if self._context.get('from_gemini'):
        raise ValidationError(_("Gemini have not create,write and delete access."))
    if not vals_list:
        return self.browse()

    self = self.browse()
    self.check_access('create')

    new_vals_list = self._prepare_create_values(vals_list)

    # classify fields for each record
    data_list = []
    determine_inverses = defaultdict(set)       # {inverse: fields}

    for vals in new_vals_list:
        precomputed = vals.pop('__precomputed__', ())

        # distribute fields into sets for various purposes
        data = {}
        data['stored'] = stored = {}
        data['inversed'] = inversed = {}
        data['inherited'] = inherited = defaultdict(dict)
        data['protected'] = protected = set()
        for key, val in vals.items():
            field = self._fields.get(key)
            if not field:
                raise ValueError("Invalid field %r on model %r" % (key, self._name))
            if field.store:
                stored[key] = val
            if field.inherited:
                inherited[field.related_field.model_name][key] = val
            elif field.inverse and field not in precomputed:
                inversed[key] = val
                determine_inverses[field.inverse].add(field)
            # protect editable computed fields and precomputed fields
            # against (re)computation
            if field.compute and (not field.readonly or field.precompute):
                protected.update(self.pool.field_computed.get(field, [field]))

        data_list.append(data)

    # create or update parent records
    for model_name, parent_name in self._inherits.items():
        parent_data_list = []
        for data in data_list:
            if not data['stored'].get(parent_name):
                parent_data_list.append(data)
            elif data['inherited'][model_name]:
                parent = self.env[model_name].browse(data['stored'][parent_name])
                parent.write(data['inherited'][model_name])

        if parent_data_list:
            parents = self.env[model_name].create([
                data['inherited'][model_name]
                for data in parent_data_list
            ])
            for parent, data in zip(parents, parent_data_list):
                data['stored'][parent_name] = parent.id

    # create records with stored fields
    records = self._create(data_list)

    # protect fields being written against recomputation
    protected = [(data['protected'], data['record']) for data in data_list]
    with self.env.protecting(protected):
        # call inverse method for each group of fields
        for fields in determine_inverses.values():
            # determine which records to inverse for those fields
            inv_names = {field.name for field in fields}
            rec_vals = [
                (data['record'], {
                    name: data['inversed'][name]
                    for name in inv_names
                    if name in data['inversed']
                })
                for data in data_list
                if not inv_names.isdisjoint(data['inversed'])
            ]

            # If a field is not stored, its inverse method will probably
            # write on its dependencies, which will invalidate the field on
            # all records. We therefore inverse the field record by record.
            if all(field.store or field.company_dependent for field in fields):
                batches = [rec_vals]
            else:
                batches = [[rec_data] for rec_data in rec_vals]

            for batch in batches:
                for record, vals in batch:
                    record._update_cache(vals)
                batch_recs = self.concat(*(record for record, vals in batch))
                next(iter(fields)).determine_inverse(batch_recs)

    # check Python constraints for non-stored inversed fields
    for data in data_list:
        data['record']._validate_fields(data['inversed'], data['stored'])

    if self._check_company_auto:
        records._check_company()

    import_module = self.env.context.get('_import_current_module')
    if not import_module: # not an import -> bail
        return records

    # It is to support setting xids directly in create by
    # providing an "id" key (otherwise stripped by create) during an import
    # (which should strip 'id' from the input data anyway)
    noupdate = self.env.context.get('noupdate', False)

    xids = (v.get('id') for v in vals_list)
    self.env['ir.model.data']._update_xmlids([
        {
            'xml_id': xid if '.' in xid else ('%s.%s' % (import_module, xid)),
            'record': rec,
            # note: this is not used when updating o2ms above...
            'noupdate': noupdate,
        }
        for rec, xid in zip(records, xids)
        if xid and isinstance(xid, str)
    ])

    return records

def write_new(self, vals: ValuesType) -> typing.Literal[True]:
    """ write(vals)

    Updates all records in ``self`` with the provided values.

    :param dict vals: fields to update and the value to set on them
    :raise AccessError: if user is not allowed to modify the specified records/fields
    :raise ValidationError: if invalid values are specified for selection fields
    :raise UserError: if a loop would be created in a hierarchy of objects a result of the operation (such as setting an object as its own parent)

    * For numeric fields (:class:`~odoo.fields.Integer`,
      :class:`~odoo.fields.Float`) the value should be of the
      corresponding type
    * For :class:`~odoo.fields.Boolean`, the value should be a
      :class:`python:bool`
    * For :class:`~odoo.fields.Selection`, the value should match the
      selection values (generally :class:`python:str`, sometimes
      :class:`python:int`)
    * For :class:`~odoo.fields.Many2one`, the value should be the
      database identifier of the record to set
    * The expected value of a :class:`~odoo.fields.One2many` or
      :class:`~odoo.fields.Many2many` relational field is a list of
      :class:`~odoo.fields.Command` that manipulate the relation the
      implement. There are a total of 7 commands:
      :meth:`~odoo.fields.Command.create`,
      :meth:`~odoo.fields.Command.update`,
      :meth:`~odoo.fields.Command.delete`,
      :meth:`~odoo.fields.Command.unlink`,
      :meth:`~odoo.fields.Command.link`,
      :meth:`~odoo.fields.Command.clear`, and
      :meth:`~odoo.fields.Command.set`.
    * For :class:`~odoo.fields.Date` and `~odoo.fields.Datetime`,
      the value should be either a date(time), or a string.

      .. warning::

        If a string is provided for Date(time) fields,
        it must be UTC-only and formatted according to
        :const:`odoo.tools.misc.DEFAULT_SERVER_DATE_FORMAT` and
        :const:`odoo.tools.misc.DEFAULT_SERVER_DATETIME_FORMAT`

    * Other non-relational fields use a string for value
    """
    if self._context.get('from_gemini'):
        raise ValidationError(_("Gemini have not create,write and delete access."))
    if not self:
        return True

    self.check_access('write')
    self.check_field_access_rights('write', vals.keys())
    env = self.env

    bad_names = {'id', 'parent_path'}
    if self._log_access:
        # the superuser can set log_access fields while loading registry
        if not(self.env.uid == SUPERUSER_ID and not self.pool.ready):
            bad_names.update(LOG_ACCESS_COLUMNS)

    # set magic fields
    vals = {key: val for key, val in vals.items() if key not in bad_names}
    if self._log_access:
        vals.setdefault('write_uid', self.env.uid)
        vals.setdefault('write_date', self.env.cr.now())

    field_values = []                           # [(field, value)]
    determine_inverses = defaultdict(list)      # {inverse: fields}
    fnames_modifying_relations = []
    protected = set()
    check_company = False
    for fname, value in vals.items():
        field = self._fields.get(fname)
        if not field:
            raise ValueError("Invalid field %r on model %r" % (fname, self._name))
        field_values.append((field, value))
        if field.inverse:
            if field.type in ('one2many', 'many2many'):
                # The written value is a list of commands that must applied
                # on the field's current value. Because the field is
                # protected while being written, the field's current value
                # will not be computed and default to an empty recordset. So
                # make sure the field's value is in cache before writing, in
                # order to avoid an inconsistent update.
                self[fname]
            determine_inverses[field.inverse].append(field)
        if self.pool.is_modifying_relations(field):
            fnames_modifying_relations.append(fname)
        if field.inverse or (field.compute and not field.readonly):
            if field.store or field.type not in ('one2many', 'many2many'):
                # Protect the field from being recomputed while being
                # inversed. In the case of non-stored x2many fields, the
                # field's value may contain unexpeced new records (created
                # by command 0). Those new records are necessary for
                # inversing the field, but should no longer appear if the
                # field is recomputed afterwards. Not protecting the field
                # will automatically invalidate the field from the cache,
                # forcing its value to be recomputed once dependencies are
                # up-to-date.
                protected.update(self.pool.field_computed.get(field, [field]))
        if fname == 'company_id' or (field.relational and field.check_company):
            check_company = True

    # force the computation of fields that are computed with some assigned
    # fields, but are not assigned themselves
    to_compute = [field.name
                  for field in protected
                  if field.compute and field.name not in vals]
    if to_compute:
        self._recompute_recordset(to_compute)

    # protect fields being written against recomputation
    with env.protecting(protected, self):
        # Determine records depending on values. When modifying a relational
        # field, you have to recompute what depends on the field's values
        # before and after modification.  This is because the modification
        # has an impact on the "data path" between a computed field and its
        # dependency.  Note that this double call to modified() is only
        # necessary for relational fields.
        #
        # It is best explained with a simple example: consider two sales
        # orders SO1 and SO2.  The computed total amount on sales orders
        # indirectly depends on the many2one field 'order_id' linking lines
        # to their sales order.  Now consider the following code:
        #
        #   line = so1.line_ids[0]      # pick a line from SO1
        #   line.order_id = so2         # move the line to SO2
        #
        # In this situation, the total amount must be recomputed on *both*
        # sales order: the line's order before the modification, and the
        # line's order after the modification.
        self.modified(fnames_modifying_relations, before=True)

        real_recs = self.filtered('id')

        # field.write_sequence determines a priority for writing on fields.
        # Monetary fields need their corresponding currency field in cache
        # for rounding values. X2many fields must be written last, because
        # they flush other fields when deleting lines.
        for field, value in sorted(field_values, key=lambda item: item[0].write_sequence):
            field.write(self, value)

        # determine records depending on new values
        #
        # Call modified after write, because the modified can trigger a
        # search which can trigger a flush which can trigger a recompute
        # which remove the field from the recompute list while all the
        # values required for the computation could not be yet in cache.
        # e.g. Write on `name` of `res.partner` trigger the recompute of
        # `display_name`, which triggers a search on child_ids to find the
        # childs to which the display_name must be recomputed, which
        # triggers the flush of `display_name` because the _order of
        # res.partner includes display_name. The computation of display_name
        # is then done too soon because the parent_id was not yet written.
        # (`test_01_website_reset_password_tour`)
        self.modified(vals)

        if self._parent_store and self._parent_name in vals:
            self.flush_model([self._parent_name])

        # validate non-inversed fields first
        inverse_fields = [f.name for fs in determine_inverses.values() for f in fs]
        real_recs._validate_fields(vals, inverse_fields)

        for fields in determine_inverses.values():
            # write again on non-stored fields that have been invalidated from cache
            for field in fields:
                if not field.store and any(self.env.cache.get_missing_ids(real_recs, field)):
                    field.write(real_recs, vals[field.name])

            # inverse records that are not being computed
            try:
                fields[0].determine_inverse(real_recs)
            except AccessError as e:
                if fields[0].inherited:
                    description = self.env['ir.model']._get(self._name).name
                    raise AccessError(_(
                        "%(previous_message)s\n\nImplicitly accessed through '%(document_kind)s' (%(document_model)s).",
                        previous_message=e.args[0],
                        document_kind=description,
                        document_model=self._name,
                    ))
                raise

        # validate inversed fields
        real_recs._validate_fields(inverse_fields)

    if check_company and self._check_company_auto:
        self._check_company()
    return True

def unlink_new(self):
    """ unlink()

    Deletes the records in ``self``.

    :raise AccessError: if the user is not allowed to delete all the given records
    :raise UserError: if the record is default property for other records
    """
    if self._context.get('from_gemini'):
        raise ValidationError(_("Gemini have not create,write and delete access."))
    if not self:
        return True

    self.check_access('unlink')

    from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG
    for func in self._ondelete_methods:
        # func._ondelete is True if it should be called during uninstallation
        if func._ondelete or not self._context.get(MODULE_UNINSTALL_FLAG):
            func(self)

    # TOFIX: this avoids an infinite loop when trying to recompute a
    # field, which triggers the recomputation of another field using the
    # same compute function, which then triggers again the computation
    # of those two fields
    for field in self._fields.values():
        self.env.remove_to_compute(field, self)

    self.env.flush_all()

    cr = self._cr
    Data = self.env['ir.model.data'].sudo().with_context({})
    Defaults = self.env['ir.default'].sudo()
    Attachment = self.env['ir.attachment'].sudo()
    ir_model_data_unlink = Data
    ir_attachment_unlink = Attachment

    # mark fields that depend on 'self' to recompute them after 'self' has
    # been deleted (like updating a sum of lines after deleting one line)
    with self.env.protecting(self._fields.values(), self):
        self.modified(self._fields, before=True)

    for sub_ids in cr.split_for_in_conditions(self.ids):
        records = self.browse(sub_ids)

        cr.execute(SQL(
            "DELETE FROM %s WHERE id IN %s",
            SQL.identifier(self._table), sub_ids,
        ))

        # Removing the ir_model_data reference if the record being deleted
        # is a record created by xml/csv file, as these are not connected
        # with real database foreign keys, and would be dangling references.
        #
        # Note: the following steps are performed as superuser to avoid
        # access rights restrictions, and with no context to avoid possible
        # side-effects during admin calls.
        data = Data.search([('model', '=', self._name), ('res_id', 'in', sub_ids)])
        ir_model_data_unlink |= data

        # For the same reason, remove the relevant records in ir_attachment
        # (the search is performed with sql as the search method of
        # ir_attachment is overridden to hide attachments of deleted
        # records)
        cr.execute(SQL(
            "SELECT id FROM ir_attachment WHERE res_model=%s AND res_id IN %s",
            self._name, sub_ids,
        ))
        ir_attachment_unlink |= Attachment.browse(row[0] for row in cr.fetchall())

        # don't allow fallback value in ir.default for many2one company dependent fields to be deleted
        # Exception: when MODULE_UNINSTALL_FLAG, these fallbacks can be deleted by Defaults.discard_records(records)
        if (many2one_fields := self.env.registry.many2one_company_dependents[self._name]) and not self._context.get(MODULE_UNINSTALL_FLAG):
            IrModelFields = self.env["ir.model.fields"]
            field_ids = tuple(IrModelFields._get_ids(field.model_name).get(field.name) for field in many2one_fields)
            sub_ids_json_text = tuple(json.dumps(id_) for id_ in sub_ids)
            if default := Defaults.search([('field_id', 'in', field_ids), ('json_value', 'in', sub_ids_json_text)], limit=1, order='id desc'):
                ir_field = default.field_id.sudo()
                field = self.env[ir_field.model]._fields[ir_field.name]
                record = self.browse(json.loads(default.json_value))
                raise UserError(_('Unable to delete %(record)s because it is used as the default value of %(field)s', record=record, field=field))

        # on delete set null/restrict for jsonb company dependent many2one
        for field in many2one_fields:
            model = self.env[field.model_name]
            if field.ondelete == 'restrict' and not self._context.get(MODULE_UNINSTALL_FLAG):
                if res := self.env.execute_query(SQL(
                    """
                    SELECT id, %(field)s
                    FROM %(table)s
                    WHERE %(field)s IS NOT NULL
                    AND %(field)s @? %(jsonpath)s
                    ORDER BY id
                    LIMIT 1
                    """,
                    table=SQL.identifier(model._table),
                    field=SQL.identifier(field.name),
                    jsonpath=f"$.* ? ({' || '.join(f'@ == {id_}' for id_ in sub_ids)})",
                )):
                    on_restrict_id, field_json = res[0]
                    to_delete_id = next(iter(id_ for id_ in field_json.values()))
                    on_restrict_record = model.browse(on_restrict_id)
                    to_delete_record = self.browse(to_delete_id)
                    raise UserError(_('You cannot delete %(to_delete_record)s, as it is used by %(on_restrict_record)s',
                                      to_delete_record=to_delete_record, on_restrict_record=on_restrict_record))
            else:
                self.env.execute_query(SQL(
                    """
                    UPDATE %(table)s
                    SET %(field)s = (
                        SELECT jsonb_object_agg(
                            key,
                            CASE
                                WHEN value::int4 in %(ids)s THEN NULL
                                ELSE value::int4
                            END)
                        FROM jsonb_each_text(%(field)s)
                    )
                    WHERE %(field)s IS NOT NULL
                    AND %(field)s @? %(jsonpath)s
                    """,
                    table=SQL.identifier(model._table),
                    field=SQL.identifier(field.name),
                    ids=sub_ids,
                    jsonpath=f"$.* ? ({' || '.join(f'@ == {id_}' for id_ in sub_ids)})",
                ))

        # For the same reason, remove the defaults having some of the
        # records as value
        Defaults.discard_records(records)

    # invalidate the *whole* cache, since the orm does not handle all
    # changes made in the database, like cascading delete!
    self.env.invalidate_all(flush=False)
    if ir_model_data_unlink:
        ir_model_data_unlink.unlink()
    if ir_attachment_unlink:
        ir_attachment_unlink.unlink()

    # auditing: deletions are infrequent and leave no trace in the database
    _unlink.info('User #%s deleted %s records with IDs: %r', self._uid, self._name, self.ids)

    return True

BaseModel.create = create_new
BaseModel.write = write_new
BaseModel.unlink = unlink_new