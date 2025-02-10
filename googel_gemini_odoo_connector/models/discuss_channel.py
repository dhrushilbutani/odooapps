from odoo import models
import google.generativeai as genai
import json
from markupsafe import Markup
class Channel(models.Model):
    _inherit = 'discuss.channel'

    def _get_question_related_to_odoo(self,gemini_model_json,message):
        format = {
            "related_to_odoo": "yes/no",
            "used_model_for_postgresql_query": "List of models"
        }
        response = gemini_model_json.generate_content(
            f'''Give me response in this format {format} for question "{message.body}"''')
        res = json.loads(response.text)
        return res

    def _generate_query(self,gemini_model_json,message,model_fields_mapping):
        format_query = '''{"query" : "postgresql_odoo_query","fields" : {"model_1" : "used_field_list_for_query_model_1","model_2": "used_field_list_for_query_model_2}}'''
        instruction = ["if field name is name use operator ilike",
                       "if field translate is true search like this : pt.name::text ilike '%abc%'",
                       "if field translate is true  and field name is name search like this : name::text ilike '%xyz%'",
                       "Must be give alias for field in generated query",
                       "for field type char and translate is true search like this where name::text ilike '%abc%'"
                       "Not take that type of word in response like [Based on the data, using the data,Based on the provided data]"]
        response = gemini_model_json.generate_content(
            f'''My database structure is {model_fields_mapping}.instruction:{instruction}.Give me response in this format {format_query} for question "{message.body}"''')
        res = json.loads(response.text)
        return res

    def _generate_python_code_snippet(self,gemini_model_json,message,used_model_for_postgresql_query):
        model_fields_mapping = dict()
        for model_name in used_model_for_postgresql_query:
            model_name = model_name.replace('_','.')
            model_id = self.env['ir.model'].search([('model', '=', model_name)], limit=1)
            if not model_id:
                continue
            field_list = [{"name": field.name, "field_description": field.field_description,
                           "type": field.ttype, "translate": field.translate}
                          for field in model_id.field_id]
            model_fields_mapping[model_name] = field_list
        if not model_fields_mapping:
            return False
        format_query = "{'python_code_snippet': [python_code_1,python_code_2,python_code_3,python_code_4,python_code_5]}"
        response = gemini_model_json.generate_content(
            f'''Key Principle:
                self Usage: You can freely use self within the code snippets, assuming the context is an Odoo model method.
                final_result Dictionary: A dictionary named final_result is always available in the environment. Store your final output in final_result['response']. Never overwrite or reassign the final_result dictionary itself. Only modify the final_result['response'] value..
                Snippet Only: Do not create classes or functions. Generate concise, directly runnable code snippets.
                ORM Methods: Use Odoo ORM methods exclusively. Do not use direct PostgreSQL queries due to access restrictions.
                Read-Only Operations: You have no access to create, write, or unlink methods.
                No sudo(): Do not use sudo() anywhere in the code.
                Context and User: Always use the following context and user when interacting with the ORM: .with_context(from_gemini=True).with_user(self.env.user). For example: self.env['sale.order'].with_context(from_gemini=True).with_user(self.env.user).search([])
                Generate 5 distinct script for odoov18.
                database structure: {model_fields_mapping}.             
                Give me response in this format {format_query} for question "{message.body}"''')
        res = json.loads(response.text)
        print(res)
        final_result = {'response' : ''}
        result_dict = {}
        for count,code in enumerate(res.get('python_code_snippet')):
            try:
                exec(code, {'self': self ,'final_result': final_result})
                result_dict[f"response_{count}"] = final_result.get('response')
            except Exception as ex:
                result_dict[f"error_{count}"] = ex
        print(result_dict)
        return result_dict

    def _get_postgresql_query(self,gemini_model_json,message,used_model_for_postgresql_query):
        model_fields_mapping = dict()
        if used_model_for_postgresql_query:
            if "product.product" in used_model_for_postgresql_query:
                used_model_for_postgresql_query.append('product.template')
        for model_name in used_model_for_postgresql_query:

            model_id = self.env['ir.model'].search([('model', '=', model_name)], limit=1)
            if not model_id:
                continue
            field_list = [{"name": field.name, "field_description": field.field_description,
                           "type": field.ttype, "translate": field.translate}
                          for field in model_id.field_id.filtered(lambda x: x.store == True)]
            model_fields_mapping[model_name] = field_list

        if not model_fields_mapping:
            return False,False
        self._generate_python_code_snippet(gemini_model_json, message, model_fields_mapping)
        res = self._generate_query(gemini_model_json,message,model_fields_mapping)
        query = res.get('query')
        self.env.cr.clear()
        try:
            self.env.cr.execute(query)
            response_postgres = self.env.cr.dictfetchall()
            return response_postgres,True
        except Exception as ex:
            return str(ex),False

    def _get_gemini_query_to_response(self,gemini_model_json,response_postgres,message):
        response_format = {"user_answer": "answer_to_user", "full_answer": "answer_to_user_based_on_question"}
        instruction = ["data is a already filtered data you don't need to filter",
                       "Only generate a preety response based on provided data",
                       "This all data is response of question"]
        response = gemini_model_json.generate_content(
            f''' data: {response_postgres},instruction:{instruction},.using this data give me answer in this format:{response_format} for this question."{message.body}"''')
        res = json.loads(response.text)
        print("_get_gemini_query_to_response",res)
        if type(res) == list and len(res) >= 1:
            res = res[0]
        return res

    def _response_gemini(self,message,gemini_model_json,user_gemini):
        response = gemini_model_json.generate_content(message)
        res = json.loads(response.text)
        if type(res) == list and len(res) >= 1:
            res = res[0]
        final_response = res.get('html_code')
        replace_list = [('<html>', ''),('</html>', ''),('html',''),("```",'')]
        for i,j in replace_list:
            final_response = final_response.replace(i, j)
        final_response.strip()

        self.with_user(user_gemini).message_post(
            body=Markup(final_response),
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )

    def _response_gemini_html(self,message,gemini_model_json,user_gemini):
        response = gemini_model_json.generate_content(message)
        res = json.loads(response.text)
        final_response =res.get('html_code')
        replace_list = [('<html>', ''), ('</html>', ''), ('html', ''), ("```", '')]
        for i, j in replace_list:
            final_response = final_response.replace(i, j)
        final_response.strip()
        print("final_response :",response.text)

        self.with_user(user_gemini).message_post(
            body=Markup(final_response),
            message_type='comment',
            subtype_xmlid='mail.mt_comment'
        )

    def _notify_thread(self, message, msg_vals=None, **kwargs):
        rdata = super(Channel, self)._notify_thread(message, msg_vals=msg_vals, **kwargs)
        partner_gemini = self.env.ref("googel_gemini_odoo_connector.partner_gemini")
        user_gemini = self.env.ref("googel_gemini_odoo_connector.user_gemini")
        author_id = msg_vals.get('author_id')
        gemini_model = self.env['ir.config_parameter'].sudo().get_param('googel_gemini_odoo_connector.gemini_model')
        gemini_api_key = self.env['ir.config_parameter'].sudo().get_param('googel_gemini_odoo_connector.gemini_api_key')
        discuss_channel_id = self.env['discuss.channel'].browse(msg_vals.get('res_id', 0))
        partner_ids = discuss_channel_id.channel_partner_ids
        try:
            if (author_id != partner_gemini.id) and (msg_vals.get('model', '') == 'discuss.channel' and partner_gemini.id in partner_ids.ids):
                channel_id = self.env['discuss.channel'].browse(msg_vals.get('res_id'))
                if channel_id.channel_type != 'chat':
                    return rdata
                if not gemini_model or not gemini_model:
                    self.with_user(user_gemini).message_post(
                        body=Markup("You haven't configured gemini api key and model. please go to setting and configure it or contact your administrator"),
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment'
                    )
                    return rdata
                genai.configure(api_key=gemini_api_key)
                gemini_model_json = genai.GenerativeModel(
                    model_name=gemini_model,
                    generation_config={"response_mime_type": "application/json"})
                res = self._get_question_related_to_odoo(gemini_model_json,message)
                print("_get_question_related_to_odoo",res)
                body = f'''generated a text answer for question : {message.body}
                           Also give a response in html code.
                         '''
                body += 'response format : {"html_code" : html_code_response}'
                if res.get('related_to_odoo') == 'yes':
                    used_model_for_postgresql_query = res.get('used_model_for_postgresql_query')
                    if used_model_for_postgresql_query and type('used_model_for_postgresql_query'):
                        try:
                            used_model_for_postgresql_query = eval(used_model_for_postgresql_query)
                        except Exception:
                            pass
                    odoo_response = self._generate_python_code_snippet(gemini_model_json,message,used_model_for_postgresql_query)
                    if odoo_response:
                        body += f'''
                                this a different response from odoo run script : {odoo_response},
                                generate a answer from this response.
                                This is a final data you don't need to filter out data.
                                Don't show a response data in answer.
                                
                        '''
                        self._response_gemini_html(body, gemini_model_json, user_gemini)
                    else:

                        self._response_gemini(body, gemini_model_json, user_gemini)
                else:
                    self._response_gemini(body,gemini_model_json,user_gemini)

        except Exception as ex:
            print(f"Error in notify thread: {str(ex)}")
        return rdata