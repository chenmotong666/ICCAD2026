module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n12,
    n30,
    n20,
    n22,
    n21,
    n31,
    n32,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  input [3:0] n12;
  output [1:0] n30;
  output n20;
  output n22;
  output n21;
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test43/test43.v:12$4_Y , \$or$testcase/test43/test43.v:13$6_Y , \$xor$testcase/test43/test43.v:14$8_Y , __fo_n2_0__, __fo_n2_1__, __fo_n2_2__, __fo_n2_3__, __fo_n3_0__, __fo_n3_1__, __fo_n3_2__, n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n116, n120, n121, n122, n123, n128, n129, n130, n131, n140, n141, n142;

  buf   fo_buf_0 (__fo_n2_0__, n2);
  buf   fo_buf_1 (__fo_n2_1__, n2);
  buf   fo_buf_2 (__fo_n2_2__, n2);
  buf   fo_buf_3 (__fo_n2_3__, n2);
  buf   fo_buf_4 (__fo_n3_0__, n3);
  buf   fo_buf_5 (__fo_n3_1__, n3);
  buf   fo_buf_6 (__fo_n3_2__, n3);
  not   \$not$testcase/test43/test43.v:21$33  (n111, n4);
  not   \$not$testcase/test43/test43.v:24$35  (n114, n4);
  not   \$not$testcase/test43/test43.v:27$17  (n116, n5);
  and   \$and$testcase/test43/test43.v:36$26  (n140, n6, n7);
  xor   \$xor$testcase/test43/test43.v:41$29  (n131, n12[0], n12[1]);
  or    \$or$testcase/test43/test43.v:43$31  (n30[1], n12[2], n12[3]);
  and   \$and$testcase/test43/test43.v:31$21  (n121, __fo_n2_2__, n4);
  and   \$and$testcase/test43/test43.v:32$22  (n122, __fo_n2_2__, n5);
  and   \$and$testcase/test43/test43.v:33$23  (n123, __fo_n2_3__, n6);
  and   \$and$testcase/test43/test43.v:16$10  (n107, __fo_n2_0__, __fo_n3_0__);
  and   \$and$testcase/test43/test43.v:19$13  (n109, __fo_n2_0__, __fo_n3_0__);
  and   \$and$testcase/test43/test43.v:20$14  (n110, __fo_n2_1__, __fo_n3_1__);
  and   \$and$testcase/test43/test43.v:30$20  (n120, __fo_n2_1__, __fo_n3_1__);
  and   \$and$testcase/test43/test43.v:8$1  (n100, __fo_n2_3__, __fo_n3_2__);
  or    \$or$testcase/test43/test43.v:37$27  (n141, n140, n8);
  xor   \$xor$testcase/test43/test43.v:35$25  (n129, n122, n123);
  or    \$or$testcase/test43/test43.v:17$11  (n108, n107, n5);
  or    \$or$testcase/test43/test43.v:34$24  (n128, n120, n121);
  or    \$or$testcase/test43/test43.v:26$15  (n22, n114, n100);
  or    \$or$testcase/test43/test43.v:9$2  (n101, n100, n4);
  not   \$not$testcase/test43/test43.v:38$36  (n142, n141);
  xor   \$xor$testcase/test43/test43.v:18$12  (n21, n108, n6);
  and   \$and$testcase/test43/test43.v:40$28  (n130, n129, n128);
  not   \$not$testcase/test43/test43.v:10$32  (n102, n101);
  and   \$and$testcase/test43/test43.v:42$30  (n30[0], n131, n130);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  xor   \$xor$testcase/test43/test43.v:11$3  (n103, n102, n5);
  and   \$and$testcase/test43/test43.v:12$4  (\$and$testcase/test43/test43.v:12$4_Y , n103, n6);
  not   \$not$testcase/test43/test43.v:12$5  (n104, \$and$testcase/test43/test43.v:12$4_Y );
  or    \$or$testcase/test43/test43.v:13$6  (\$or$testcase/test43/test43.v:13$6_Y , n104, n7);
  not   \$not$testcase/test43/test43.v:13$7  (n105, \$or$testcase/test43/test43.v:13$6_Y );
  xor   \$xor$testcase/test43/test43.v:14$8  (\$xor$testcase/test43/test43.v:14$8_Y , n105, n8);
  not   \$not$testcase/test43/test43.v:14$9  (n20, \$xor$testcase/test43/test43.v:14$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = n5;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
