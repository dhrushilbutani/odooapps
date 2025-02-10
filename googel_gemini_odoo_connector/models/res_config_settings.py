from odoo import models,fields

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gemini_model = fields.Selection(
        [('gemini-2.0-flash-exp','Gemini 2.0 Flash'),
         ('gemini-1.5-flash-8b','Gemini 1.5 Flash-8B'),
         ('gemini-1.5-pro','Gemini 1.5 Pro')],
        string='Gemini Model', config_parameter='googel_gemini_odoo_connector.gemini_model')

    gemini_api_key = fields.Char(string='Gemini API Key',config_parameter='googel_gemini_odoo_connector.gemini_api_key')